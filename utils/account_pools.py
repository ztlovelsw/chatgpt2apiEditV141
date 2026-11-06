from typing import Literal

AccountPool = Literal["default", "free", "plus", "pro", "gptfree"]
POOL_PLAN_TYPES = {
    "free": ("free",),
    "plus": ("plus", "team", "business", "go"),
    "pro": ("pro", "prolite", "enterprise"),
}


def image_pool(account: dict) -> str:
    plan = str(account.get("type") or "free").strip().lower().replace("_", "").replace("-", "")
    for pool, plans in POOL_PLAN_TYPES.items():
        if plan in plans:
            return pool
    return "plus"
