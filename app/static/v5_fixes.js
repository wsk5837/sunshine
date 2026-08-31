/* V5.0 targeted fixes: multi-budget validation + budget approval UI. */
(() => {
  window.budgetSnapshot = function budgetSnapshotV5(snapshot) {
    if (!snapshot) return '<div class="callout warn">当前需求尚未关联可校验的预算。</div>';
    const items = Array.isArray(snapshot.items) && snapshot.items.length ? snapshot.items : [snapshot];
    const cards = items.map((s) => `<div class="section flat" style="margin:10px 0">
      <div class="toolbar"><div><strong>${esc(s.budget_name || '')}</strong><div class="sub">${esc(s.budget_no || '')}</div></div><span class="status ${s.sufficient ? 'success' : 'danger'}">${s.sufficient ? '校验通过' : '预算不足'}</span></div>
      <div class="grid-4" style="margin-top:10px">
        <div class="metric soft"><div class="k">总预算</div><div class="v">¥${money(s.total_budget)}</div></div>
        <div class="metric soft"><div class="k">已使用</div><div class="v">¥${money(s.used_budget)}</div><div class="sub">${s.execution_rate || 0}%</div></div>
        <div class="metric soft"><div class="k">剩余可用</div><div class="v">¥${money(s.remaining_budget)}</div></div>
        <div class="metric soft"><div class="k">本次分摊</div><div class="v">¥${money(s.current_demand_amount)}</div><div class="sub">审批后 ${s.after_execution_rate || 0}%</div></div>
      </div>
      <div class="progress" style="margin-top:10px"><span style="width:${Math.min(100, Number(s.after_execution_rate || 0))}%"></span></div>
      <div class="callout ${!s.sufficient ? 'danger' : s.warning ? 'warn' : 'success'}" style="margin-top:10px">${s.sufficient ? '✓ 当前费用出处预算可用' : '⚠ 当前费用出处预算不足'}${s.warning ? '；本次分摊后执行率达到95%预警阈值。' : ''}</div>
    </div>`).join('');
    const total = Number(snapshot.current_demand_total ?? items.reduce((sum, x) => sum + Number(x.current_demand_amount || 0), 0));
    const allOk = snapshot.all_sufficient ?? items.every((x) => x.sufficient);
    return `<div class="callout ${allOk ? 'success' : 'danger'}"><strong>${allOk ? '✓ 预算校验通过' : '⚠ 预算校验未通过'}</strong> · 共 ${items.length} 个费用出处，本次需求合计 ¥${money(total)}</div>${cards}`;
  };

  window.updateBudgetPreview = function updateBudgetPreviewV5() {
    const preview = $('#budgetPreview');
    if (!preview) return;
    const selected = getFormBudgetSources();
    if (!selected.length) {
      preview.innerHTML = '<div class="empty" style="min-height:120px">选择预算出处后展示实时预算信息</div>';
      return;
    }
    const budgets = selected.map((name) => state.meta.budgets.find((b) => b.budget_name === name)).filter(Boolean);
    preview.innerHTML = budgets.map((b) => {
      const rate = b.total_budget ? Number(b.used_budget || 0) / Number(b.total_budget) * 100 : 0;
      return `<div class="callout" style="margin-bottom:8px"><strong>${esc(b.budget_name)}</strong><div class="sub">${esc(b.budget_no)} · 总预算 ¥${money(b.total_budget)} · 已使用 ¥${money(b.used_budget)} · 剩余 ¥${money(Number(b.total_budget)-Number(b.used_budget))} · 执行率 ${rate.toFixed(2)}%</div></div>`;
    }).join('') + '<div class="help">最终校验金额以“费用评估与预算”中的分摊比例和费用出处为准。</div>';
  };

  const originalRenderBudget = window.renderBudget;
  window.renderBudget = async function renderBudgetV5() {
    await originalRenderBudget();
    try {
      const ledger = (await api('/api/budget-ledger')).data || [];
      const byNo = new Map(ledger.map((b) => [b.budget_no, b]));
      $$('#budgetRows tr').forEach((tr) => {
        const no = tr.children[0]?.textContent?.trim();
        const b = byNo.get(no);
        if (!b) return;
        const statusCell = tr.children[7];
        if (statusCell) statusCell.innerHTML = statusPill(b.status || '已生效');
        const actionCell = tr.children[8];
        if (actionCell && b.status !== '已生效') {
          actionCell.innerHTML = `<button class="link budget-view" data-id="${b.id}">详情</button>${b.status === '草稿' || b.status === '已驳回' ? ` <button class="link budget-submit" data-id="${b.id}">提交审批</button>` : ''}`;
        }
      });
      $$('.budget-submit').forEach((button) => button.addEventListener('click', async () => {
        try { await api(`/api/budgets/${button.dataset.id}/submit`, { method: 'POST' }); toast('预算已提交审批', 'success'); renderBudget(); }
        catch (e) { toast(e.message, 'error'); }
      }));

      const newButton = $('#newBudget');
      if (newButton) {
        const replacement = newButton.cloneNode(true);
        newButton.replaceWith(replacement);
        replacement.addEventListener('click', () => {
          showModal(`<h3>新增预算</h3>
            <div class="form-row"><div class="label required">预算名称</div><input id="v5BName" class="field"></div>
            <div class="grid-2"><div><label>总预算</label><input id="v5BTotal" class="field" type="number" min="0" value="0"></div><div><label>年度</label><input id="v5BYear" class="field" type="number" value="2026"></div></div>
            <div class="grid-2" style="margin-top:10px"><div><label>内部研发预算</label><input id="v5BInternal" class="field" type="number" min="0" value="0"></div><div><label>委托数科预算</label><input id="v5BDigital" class="field" type="number" min="0" value="0"></div></div>
            <div class="callout" style="margin-top:12px">新增预算不会立即生效。提交后进入财务审批，审批通过后才可用于需求预算出处和费用分摊。</div>
            <div class="modal-actions">${btn('取消','btn','v5BCancel')}${btn('保存草稿','btn secondary','v5BDraft')}${btn('保存并提交审批','btn primary','v5BSubmit')}</div>`);
          $('#v5BCancel').onclick = closeModal;
          const save = async (submit) => {
            try {
              const p = { budget_no: null, budget_name: $('#v5BName').value.trim(), total_budget: Number($('#v5BTotal').value || 0), used_budget: 0, internal_total: Number($('#v5BInternal').value || 0), internal_used: 0, digital_total: Number($('#v5BDigital').value || 0), digital_used: 0, year: Number($('#v5BYear').value || 2026) };
              const created = await api('/api/budgets', { method: 'POST', body: JSON.stringify(p) });
              if (submit) await api(`/api/budgets/${created.data.id}/submit`, { method: 'POST' });
              closeModal(); toast(submit ? '预算已提交财务审批' : '预算草稿已保存', 'success'); renderBudget();
            } catch (e) { toast(e.message, 'error'); }
          };
          $('#v5BDraft').onclick = () => save(false);
          $('#v5BSubmit').onclick = () => save(true);
        });
      }

      let pending = [];
      try { pending = (await api('/api/budget-approvals/pending')).data || []; } catch (_) { pending = []; }
      if (pending.length) {
        const section = document.createElement('div');
        section.className = 'section';
        section.style.marginTop = '12px';
        section.innerHTML = `<div class="section-title">待审批预算</div>${simpleTable(['预算编号','预算名称','年度','总预算','申请人','操作'], pending.map((b) => [esc(b.budget_no), esc(b.budget_name), b.year, `¥${money(b.total_budget)}`, esc(b.applicant || '—'), `<button class="link budget-approve-v5" data-id="${b.id}" data-action="通过">通过</button> <button class="link danger budget-approve-v5" data-id="${b.id}" data-action="驳回">驳回</button>`]))}`;
        appView.appendChild(section);
        $$('.budget-approve-v5').forEach((button) => button.addEventListener('click', () => {
          const action = button.dataset.action;
          showModal(`<h3>预算审批 · ${action}</h3><label>审批意见<textarea id="v5BudgetComment" class="textarea" placeholder="请输入审批意见"></textarea></label><div class="modal-actions">${btn('取消','btn','v5BudgetCancel')}${btn(action, action === '通过' ? 'btn primary' : 'btn danger','v5BudgetConfirm')}</div>`);
          $('#v5BudgetCancel').onclick = closeModal;
          $('#v5BudgetConfirm').onclick = async () => {
            try { await api(`/api/budgets/${button.dataset.id}/approve`, { method:'POST', body:JSON.stringify({action, comment:$('#v5BudgetComment').value}) }); closeModal(); toast(`预算已${action}`, 'success'); renderBudget(); }
            catch (e) { toast(e.message, 'error'); }
          };
        }));
      }
    } catch (e) {
      console.error('V5 budget patch failed', e);
    }
  };
})();

