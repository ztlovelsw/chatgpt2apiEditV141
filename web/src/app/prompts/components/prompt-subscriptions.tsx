"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowLeft, LoaderCircle, Pause, Pencil, Play, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  createPromptSubscription, deletePromptSubscription, fetchPromptSubscriptions, syncPromptSubscription,
  updatePromptSubscription, type PromptSubscription, type PromptSubscriptionResponse,
} from "@/lib/api";

const intervalUnits = {
  minutes: { label: "分钟", minutes: 1 },
  hours: { label: "小时", minutes: 60 },
  days: { label: "天", minutes: 1440 },
} as const;
type IntervalUnit = keyof typeof intervalUnits;
const emptyForm = { id: "", name: "", url: "", interval: "12", unit: "hours" as IntervalUnit };
const statusLabels = { idle: "待同步", queued: "等待同步", syncing: "正在同步", success: "同步成功", error: "同步失败" };

function intervalForm(minutes: number) {
  const unit: IntervalUnit = minutes % 1440 === 0 ? "days" : minutes % 60 === 0 ? "hours" : "minutes";
  return { interval: String(minutes / intervalUnits[unit].minutes), unit };
}

function displayInterval(minutes: number) {
  const { interval, unit } = intervalForm(minutes);
  return `${interval} ${intervalUnits[unit].label}`;
}

function displayTime(value: number | null) {
  return value ? new Date(value * 1000).toLocaleString("zh-CN", { hour12: false }) : "—";
}

type PromptSubscriptionsProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onBack: () => void;
  onLibraryChange: () => void;
};

export function PromptSubscriptions({ open, onOpenChange, onBack, onLibraryChange }: PromptSubscriptionsProps) {
  const [items, setItems] = useState<PromptSubscription[]>([]);
  const [form, setForm] = useState(emptyForm);
  const [busyId, setBusyId] = useState("");
  const [loadError, setLoadError] = useState("");
  const [revision, setRevision] = useState(0);
  const lastSync = useRef<string | null>(null);
  const requestVersion = useRef(0);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      let delay = 15000;
      try {
        const version = requestVersion.current;
        const data = await fetchPromptSubscriptions();
        if (!active || version !== requestVersion.current) return;
        setItems(data.items);
        setLoadError("");
        const signature = JSON.stringify(data.items.map((item) => [item.id, item.last_sync_at]));
        if (signature !== lastSync.current && (lastSync.current !== null || data.items.some((item) => item.last_success_at !== null))) onLibraryChange();
        lastSync.current = signature;
        if (data.items.some((item) => item.status === "syncing" || item.status === "queued")) delay = 1500;
      } catch (error) {
        if (active) setLoadError(error instanceof Error ? error.message : "读取订阅失败");
      } finally {
        if (active) timer = setTimeout(() => void poll(), delay);
      }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [onLibraryChange, revision]);

  const action = async (id: string, run: () => Promise<PromptSubscriptionResponse>, message: string) => {
    requestVersion.current += 1;
    setBusyId(id);
    try {
      const data = await run();
      requestVersion.current += 1;
      setItems(data.items);
      setRevision((value) => value + 1);
      toast.success(message);
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "操作失败");
      return false;
    } finally {
      setBusyId("");
    }
  };

  const save = async () => {
    const interval = Number(form.interval) * intervalUnits[form.unit].minutes;
    if (!Number.isInteger(interval) || interval < 5 || interval > 10080) {
      toast.error("更新间隔应为 5 分钟至 7 天，并精确到整分钟");
      return;
    }
    const payload = { name: form.name.trim(), interval_minutes: interval };
    const success = await action(form.id || "new", () => form.id
      ? updatePromptSubscription(form.id, payload)
      : createPromptSubscription({ ...payload, url: form.url.trim() }), form.id ? "订阅已更新" : "订阅已添加，首次同步已排队");
    if (success) setForm(emptyForm);
  };

  const remove = async (item: PromptSubscription) => {
    if (!window.confirm(`删除订阅「${item.name}」？已同步的提示词会保留为普通提示词。`)) return;
    if (await action(item.id, () => deletePromptSubscription(item.id), "订阅已删除，已同步内容已保留")) {
      if (form.id === item.id) setForm(emptyForm);
      onLibraryChange();
    }
  };

  const close = () => {
    if (busyId) return;
    setForm(emptyForm);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) close(); }}>
      <DialogContent showCloseButton={!busyId} className="flex max-h-[90dvh] w-[min(94vw,1040px)] flex-col gap-0 overflow-hidden rounded-2xl p-0">
        <DialogHeader className="shrink-0 border-b border-stone-200 px-5 py-5 pr-12 sm:px-6">
          <DialogTitle>提示词订阅</DialogTitle>
          <DialogDescription className="leading-6">添加和管理多个订阅来源，自动同步提示词与示例图。默认每 12 小时更新，支持自定义间隔。</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 overflow-y-auto px-5 py-5 sm:px-6">
          <h3 className="text-sm font-semibold text-stone-900">{form.id ? "编辑订阅" : "添加订阅"}</h3>
          <fieldset disabled={!!busyId} className="mt-3 grid min-w-0 gap-2 sm:grid-cols-2 lg:grid-cols-[minmax(120px,1fr)_minmax(180px,2fr)_210px_auto]">
            <Input aria-label="订阅名称" placeholder="订阅名称，例如：水仙提示词" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
            <Input aria-label="订阅链接" type="url" placeholder="网页、JSON、Markdown 或水仙仓库链接" value={form.url} disabled={!!form.id} onChange={(event) => setForm({ ...form, url: event.target.value })} className="min-w-0" />
            <div className="flex items-center gap-2 text-xs text-stone-500">
              <span className="shrink-0">每</span>
              <Input aria-label="更新间隔" type="number" min={5 / intervalUnits[form.unit].minutes} max={10080 / intervalUnits[form.unit].minutes} step={form.unit === "minutes" ? 1 : "any"} value={form.interval} onChange={(event) => setForm({ ...form, interval: event.target.value })} className="min-w-0" />
              <Select value={form.unit} disabled={!!busyId} onValueChange={(unit) => setForm({ ...form, unit: unit as IntervalUnit })}>
                <SelectTrigger aria-label="更新时间单位" className="w-20 shrink-0"><SelectValue /></SelectTrigger>
                <SelectContent>{Object.entries(intervalUnits).map(([unit, option]) => <SelectItem key={unit} value={unit}>{option.label}</SelectItem>)}</SelectContent>
              </Select>
            </div>
            <div className="flex gap-2">
              <Button disabled={!form.url.trim()} onClick={() => void save()} className="bg-stone-950 text-white hover:bg-stone-800">{busyId === (form.id || "new") ? <LoaderCircle className="size-4 animate-spin" /> : null}{form.id ? "保存订阅" : "添加订阅"}</Button>
              {form.id ? <Button variant="outline" onClick={() => setForm(emptyForm)}>取消</Button> : null}
            </div>
          </fieldset>
          {loadError ? <p role="alert" className="mt-2 text-xs text-rose-600">{loadError}</p> : null}
          <h3 className="mt-6 text-sm font-semibold text-stone-900">已订阅来源 · {items.length}</h3>
          {items.length === 0 && !loadError ? <p className="mt-3 rounded-xl border border-dashed border-stone-200 px-4 py-6 text-center text-sm text-stone-500">暂无订阅，添加链接后会自动开始同步。</p> : null}
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {items.map((item) => {
              const running = item.status === "syncing";
              const disabled = !!busyId || running;
              return (
                <div key={item.id} data-subscription-id={item.id} className="min-w-0 rounded-xl border border-stone-200 bg-white p-3">
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0 truncate text-sm font-semibold text-stone-900">{item.name}</span>
                    <span className={`shrink-0 text-xs ${item.last_error ? "text-rose-600" : "text-stone-500"}`}>{running ? <LoaderCircle className="mr-1 inline size-3 animate-spin" /> : null}{!item.enabled ? "自动更新已暂停 · " : ""}{statusLabels[item.status]}</span>
                  </div>
                  <a href={item.url} target="_blank" rel="noopener noreferrer" className="mt-1 block truncate text-xs text-stone-500 hover:underline">{item.url}</a>
                  <div className="mt-2 space-y-1 text-xs text-stone-500">
                    <p>每 {displayInterval(item.interval_minutes)} · 上次同步：{displayTime(item.last_sync_at)}</p>
                    <p>下次自动同步：{displayTime(item.next_sync_at)}</p>
                    {item.last_result ? <p>来源 {item.last_result.total} 条 · 新增 {item.last_result.added} · 更新 {item.last_result.updated} · 未变 {item.last_result.unchanged} · 保留本地操作 {item.last_result.skipped}</p> : null}
                    {item.last_error ? <p role="alert" className="break-words text-rose-600">{item.last_error}</p> : null}
                    {item.warnings.map((warning) => <p key={warning} className="text-amber-700">{warning}</p>)}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" disabled={disabled || item.status === "queued"} onClick={() => void action(item.id, () => syncPromptSubscription(item.id), "同步已排队")}><RefreshCw className="size-3.5" />立即同步</Button>
                    <Button size="sm" variant="outline" disabled={disabled} onClick={() => void action(item.id, () => updatePromptSubscription(item.id, { enabled: !item.enabled }), item.enabled ? "自动更新已暂停" : "自动更新已恢复")}>{item.enabled ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}{item.enabled ? "暂停" : "恢复"}</Button>
                    <Button size="sm" variant="outline" aria-label={`编辑订阅 ${item.name}`} disabled={disabled} onClick={() => setForm({ id: item.id, name: item.name, url: item.url, ...intervalForm(item.interval_minutes) })}><Pencil className="size-3.5" />编辑</Button>
                    <Button size="sm" variant="outline" aria-label={`删除订阅 ${item.name}`} disabled={disabled} onClick={() => void remove(item)}><Trash2 className="size-3.5 text-rose-600" />删除</Button>
                  </div>
                </div>
              );
            })}
          </div>
          <p className="mt-3 text-xs leading-5 text-stone-500">同步会更新同来源的已有条目。手动编辑或删除的条目会保留你的操作；来源移除的条目及取消订阅后的内容也会保留。</p>
        </div>
        <DialogFooter className="shrink-0 border-t border-stone-200 px-5 py-4 sm:justify-between sm:px-6">
          <Button variant="ghost" disabled={!!busyId} onClick={() => { setForm(emptyForm); onBack(); }}><ArrowLeft className="size-4" />返回选择方式</Button>
          <Button variant="secondary" disabled={!!busyId} className="rounded-xl" onClick={close}>完成</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
