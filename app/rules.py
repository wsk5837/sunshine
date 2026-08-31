from dataclasses import dataclass
from typing import Optional

DEMAND_TYPES = ["业务流程优化", "智能化改造项目", "系统功能新增"]
PRIORITIES = ["高", "中", "低"]
ATTACHMENT_EXTS = {"doc", "docx", "xls", "xlsx", "pdf", "png", "jpg", "jpeg", "zip"}
MAX_ATTACHMENT_COUNT = 10
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 2000
MAX_TITLE_LEN = 100
MAX_DESCRIPTION_LEN = 5000
MAX_AI_LEN = 1000
MAX_BUDGET_SOURCES = 20
MAX_ALLOCATION_ROWS = 500
MAX_FP_PER_DEMAND = 200

APPROVAL_FLOW = {
    "直属领导审批": ("department_head", "产品经理审批"),
    "产品经理审批": ("product_manager", "财务审批"),
    "财务审批": ("finance", None),
    "分管总审批": ("vp", "终审"),
    "终审": ("business_owner", "审批通过"),
}

ROLE_LABELS = {
    "applicant": "需求申请人",
    "department_head": "部门负责人",
    "product_manager": "产品经理",
    "finance": "财务人员",
    "vp": "分管总",
    "business_owner": "业务负责人",
    "project_manager": "项目经理",
    "admin": "系统管理员",
}

TAPD_STATUS_MAP = {
    "新": "已创建",
    "开发中": "开发中",
    "测试中": "测试中",
    "已验收": "待发布",
    "已关闭": "已完成",
    "已拒绝": "已终止",
}


@dataclass
class BusinessError(Exception):
    http_status: int
    code: str
    message: str
    details: Optional[dict] = None


def validate_title(title: str):
    title = (title or "").strip()
    if not title:
        raise BusinessError(400, "REQ-4002", "需求标题不能为空")
    if len(title) > MAX_TITLE_LEN:
        raise BusinessError(400, "REQ-4003", f"需求标题不能超过{MAX_TITLE_LEN}个字符")
    return title


def validate_description(description: str):
    if len(description or "") > MAX_DESCRIPTION_LEN:
        raise BusinessError(400, "REQ-4003", f"需求描述不能超过{MAX_DESCRIPTION_LEN}个字符")


def validate_common(demand_type: str, priority: str, budget_sources: list[str]):
    if demand_type not in DEMAND_TYPES:
        raise BusinessError(400, "REQ-4001", "需求类型不在有效字典范围内")
    if priority not in PRIORITIES:
        raise BusinessError(400, "REQ-4001", "优先级必须为高/中/低")
    if len(budget_sources or []) > MAX_BUDGET_SOURCES:
        raise BusinessError(400, "REQ-4003", f"预算出处最多选择{MAX_BUDGET_SOURCES}项")
