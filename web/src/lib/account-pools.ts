import type { Account, AccountPool } from "@/lib/api";

export const IMAGE_POOL_OPTIONS = [
  { value: "default", label: "自动（Free 优先）" },
  { value: "free", label: "GPT Free 号池" },
  { value: "plus", label: "Plus 号池" },
  { value: "pro", label: "Pro 号池" },
] as const;

export function accountImagePool(account: Account): "free" | "plus" | "pro" {
  if (account.image_pool) return account.image_pool;
  const plan = String(account.type || "free").toLowerCase().replace(/[_-]/g, "");
  if (plan === "free") return "free";
  return ["pro", "prolite", "enterprise"].includes(plan) ? "pro" : "plus";
}

export function normalizeImageAccountPool(value: unknown): AccountPool {
  if (value === "gptfree") return "free";
  return value === "free" || value === "plus" || value === "pro" ? value : "default";
}

export function poolAccounts(accounts: Account[], pool: AccountPool) {
  return accounts.filter((account) => {
    if (account.source_type === "gptfree") return pool === "gptfree";
    return pool === "default" || accountImagePool(account) === pool;
  });
}

export function poolQuota(accounts: Account[], pool: AccountPool) {
  const ready = poolAccounts(accounts, pool).filter((account) => account.status === "正常");
  if (ready.some((account) => account.image_quota_unknown)) return "未知";
  return String(ready.reduce((sum, account) => sum + Math.max(0, account.quota), 0));
}
