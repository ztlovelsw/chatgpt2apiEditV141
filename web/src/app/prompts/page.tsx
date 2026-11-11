"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ChevronRight, Copy, Download, Link2, LoaderCircle, Pencil, Plus, Rss, Search, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { ImageLightbox } from "@/components/image-lightbox";
import { PromptExampleImage } from "@/components/prompt-example-image";
import {
  createPromptLibraryItem,
  deletePromptLibraryItem,
  fetchPromptLibrary,
  importPromptLibraryUrl,
  updatePromptLibraryItem,
  type PromptLibraryDraft,
  type PromptLibraryImportResponse,
  type PromptLibraryItem,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { copyTextToClipboard } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import { PromptSubscriptions } from "./components/prompt-subscriptions";

type PromptForm = {
  id?: string;
  name: string;
  type: string;
  content: string;
  note: string;
  source_url: string;
  example_images: string[];
};

const emptyForm: PromptForm = {
  name: "",
  type: "默认",
  content: "",
  note: "",
  source_url: "",
  example_images: [],
};

export default function PromptsPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin", "user"]);
  const [items, setItems] = useState<PromptLibraryItem[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [form, setForm] = useState<PromptForm>(emptyForm);
  const [dialogMode, setDialogMode] = useState<"menu" | "subscriptions" | "custom" | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [importUrl, setImportUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<(PromptLibraryImportResponse & { url: string; query: string }) | null>(null);
  const [importQuery, setImportQuery] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [previewImages, setPreviewImages] = useState<string[]>([]);
  const [previewIndex, setPreviewIndex] = useState(0);
  const busy = saving || importing;
  const importedItems = importResult?.items || [];
  const importWarnings = importResult?.warnings || [];

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const data = await fetchPromptLibrary();
      setItems(data.items);
      setTypes(data.types);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载提示词失败");
    } finally {
      setLoading(false);
    }
  }, []);
  const refreshLibrary = useCallback(() => { void load(true); }, [load]);

  useEffect(() => {
    if (!session) return;
    void load();
  }, [session, load]);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return items.filter((item) => {
      const matchesType = typeFilter === "all" || item.type === typeFilter;
      const haystack = `${item.name}\n${item.type}\n${item.content}\n${item.note || ""}\n${item.source_url || ""}`.toLowerCase();
      return Boolean(item.example_images?.length) && matchesType && (!keyword || haystack.includes(keyword));
    });
  }, [items, query, typeFilter]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / 30));
  const currentPage = Math.min(page, pageCount);
  const visibleItems = filtered.slice((currentPage - 1) * 30, currentPage * 30);

  const resetForm = () => {
    setForm(emptyForm);
    setImageUrl("");
  };

  const selectDraft = (item: PromptLibraryDraft, id?: string) => {
    setForm({ ...emptyForm, ...item, id, note: item.note || "", source_url: item.source_url || "", example_images: item.example_images || [] });
    setImageUrl("");
  };

  const importFromUrl = async (options?: { page: number; query: string }) => {
    const url = options ? importResult!.url : importUrl.trim();
    const sourceQuery = options?.query || "";
    if (!url) {
      toast.error("请填写要拉取的链接");
      return;
    }
    setImporting(true);
    try {
      const data = await importPromptLibraryUrl(url, { page: options?.page || 1, query: sourceQuery });
      setImportResult({ ...data, url, query: sourceQuery });
      if (!options) {
        setImportQuery("");
        if (data.items[0]) selectDraft(data.items[0]);
        toast.success(`已识别 ${data.total ?? data.items.length} 条提示词，请核对后保存`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "拉取失败");
    } finally {
      setImporting(false);
    }
  };

  const addExampleImage = () => {
    try {
      const url = new URL(imageUrl.trim());
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error();
      if (form.example_images.includes(url.href)) {
        toast.info("这张示例图已添加");
        return;
      }
      if (form.example_images.length >= 12) {
        toast.error("每条提示词最多保存 12 张示例图");
        return;
      }
      setForm({ ...form, example_images: [...form.example_images, url.href] });
      setImageUrl("");
    } catch {
      toast.error("请填写有效的 HTTP 或 HTTPS 图片链接");
    }
  };

  const openPreview = (images: string[], index: number) => {
    setPreviewImages(images);
    setPreviewIndex(index);
  };

  const save = async () => {
    if (!form.content.trim()) {
      toast.error("请填写提示词内容");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim() || form.content.trim().slice(0, 24),
        type: form.type.trim() || "默认",
        content: form.content,
        note: form.note,
        source_url: form.source_url,
        example_images: form.example_images,
      };
      const data = form.id
        ? await updatePromptLibraryItem(form.id, payload)
        : await createPromptLibraryItem(payload);
      setItems(data.items);
      setTypes(data.types);
      resetForm();
      setDialogMode(null);
      toast.success(form.id ? "提示词已更新" : "提示词已保存");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const copyPrompt = async (content: string) => {
    try {
      await copyTextToClipboard(content);
      toast.success("提示词已复制");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "复制失败");
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm("确认删除这个提示词吗？")) return;
    try {
      const data = await deletePromptLibraryItem(id);
      setItems(data.items);
      setTypes(data.types);
      if (form.id === id) resetForm();
      toast.success("已删除");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  const clearImport = () => {
    setImportUrl("");
    setImportQuery("");
    setImportResult(null);
  };

  const openNew = () => {
    resetForm();
    clearImport();
    setDialogMode("menu");
  };

  const editPrompt = (item: PromptLibraryItem) => {
    selectDraft(item, item.id);
    clearImport();
    setDialogMode("custom");
  };

  const closeDialog = () => {
    if (!busy) setDialogMode(null);
  };

  if (isCheckingAuth || !session) {
    return (
      <div className="flex h-full items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return (
    <Dialog open={dialogMode === "menu" || dialogMode === "custom"} onOpenChange={(open) => { if (open) openNew(); else closeDialog(); }}>
      <section className="mx-auto flex h-full min-h-0 w-full max-w-[1440px] flex-col gap-4 overflow-hidden px-1 py-4 sm:px-3">
        <div className="flex shrink-0 flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-stone-950">提示词</h1>
            <p className="mt-1 text-sm text-stone-500">浏览示例图片，按名称或分类快速查找。</p>
          </div>
          <DialogTrigger asChild>
            <Button className="h-10 shrink-0 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800" disabled={busy}>
              <Plus className="size-4" />新建提示词
            </Button>
          </DialogTrigger>
        </div>

        <div className="flex min-h-0 min-w-0 flex-1 flex-col rounded-2xl border border-stone-200 bg-white/80 p-4 shadow-[0_18px_70px_-52px_rgba(15,23,42,0.45)] backdrop-blur">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-stone-400" />
              <Input aria-label="搜索提示词" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="搜索名称、内容或备注" className="rounded-xl border-stone-200 bg-white pl-9" />
            </div>
            <Select value={typeFilter} onValueChange={(value) => { setTypeFilter(value); setPage(1); }}>
              <SelectTrigger className="w-full rounded-xl border-stone-200 bg-white sm:w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部类型</SelectItem>
                {types.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="mb-3 flex items-center justify-between gap-2 text-xs text-stone-500">
            <span>共 {filtered.length} 条 · 第 {currentPage}/{pageCount} 页</span>
            <div className="flex gap-2"><Button size="sm" variant="outline" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>上一页</Button><Button size="sm" variant="outline" disabled={currentPage === pageCount} onClick={() => setPage(currentPage + 1)}>下一页</Button></div>
          </div>

          <div aria-label="提示词列表" key={`${currentPage}:${query}:${typeFilter}`} className="min-h-0 flex-1 overflow-y-auto pr-1">
            {loading ? (
              <div className="flex h-40 items-center justify-center"><LoaderCircle className="size-5 animate-spin text-stone-400" /></div>
            ) : filtered.length === 0 ? (
              <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-stone-200 bg-stone-50 text-sm text-stone-500">暂无示例图片</div>
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {visibleItems.flatMap((item) =>
                  (item.example_images || []).map((url, index) => (
                    <div key={`${item.id}:${index}`} className="group relative min-w-0 overflow-hidden rounded-xl border border-stone-200 bg-white">
                      <button type="button" aria-label={`预览 ${item.name} 示例图 ${index + 1}`} className="block w-full cursor-zoom-in" onClick={() => openPreview(item.example_images || [], index)}>
                        <PromptExampleImage src={url} alt={`${item.name} 示例效果图 ${index + 1}`} className="aspect-square w-full rounded-none" />
                      </button>
                      <div className="absolute right-2 top-2 flex gap-1 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
                        <Button variant="outline" size="sm" aria-label={`复制 ${item.name}`} title="复制提示词" className="size-8 rounded-lg border-stone-200 bg-white/95 p-0" onClick={() => void copyPrompt(item.content)}>
                          <Copy className="size-3.5" />
                        </Button>
                        <Button variant="outline" size="sm" aria-label={`编辑 ${item.name}`} title="编辑提示词" disabled={busy} className="size-8 rounded-lg border-stone-200 bg-white/95 p-0" onClick={() => editPrompt(item)}>
                          <Pencil className="size-3.5" />
                        </Button>
                        <Button variant="outline" size="sm" aria-label={`删除 ${item.name}`} title="删除提示词" disabled={busy} className="size-8 rounded-lg border-stone-200 bg-white/95 p-0 text-rose-600" onClick={() => void remove(item.id)}>
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      </section>

      <DialogContent showCloseButton={!busy} className={cn("flex max-h-[90dvh] flex-col gap-0 overflow-hidden rounded-2xl p-0", dialogMode === "custom" ? "w-[min(94vw,800px)]" : "w-[min(92vw,560px)]")}>
        <DialogHeader className="shrink-0 border-b border-stone-200 px-5 py-5 pr-12 sm:px-6">
          <DialogTitle>{dialogMode === "menu" ? "新建提示词" : form.id ? "编辑提示词" : "自定义添加提示词"}</DialogTitle>
          <DialogDescription className="leading-6">{dialogMode === "menu" ? "选择添加方式。" : "填写提示词内容和示例图，也可以从链接导入后编辑保存。"}</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 overflow-y-auto px-5 py-5 sm:px-6">
          {dialogMode === "menu" ? (
            <div className="space-y-3">
              {[
                { mode: "subscriptions" as const, title: "提示词订阅", description: "添加或管理订阅链接，定时更新提示词与示例图。", icon: Rss },
                { mode: "custom" as const, title: "自定义添加提示词", description: "手动编写提示词，添加示例图，或从链接导入内容。", icon: Pencil },
              ].map(({ mode, title, description, icon: Icon }) => (
                <button key={mode} type="button" aria-label={title} onClick={() => setDialogMode(mode)} className="flex w-full items-start gap-4 rounded-2xl border border-stone-200 bg-white p-4 text-left transition hover:border-stone-300 hover:bg-stone-50">
                  <span className="shrink-0 rounded-xl bg-stone-100 p-3 text-stone-700"><Icon className="size-5" /></span>
                  <span className="min-w-0 flex-1 space-y-1"><span className="block text-sm font-semibold text-stone-900">{title}</span><span className="block text-sm leading-6 text-stone-500">{description}</span></span>
                  <ChevronRight className="mt-3 size-4 shrink-0 text-stone-400" />
                </button>
              ))}
            </div>
          ) : dialogMode === "custom" ? (
            <div className="min-w-0">
              <details className="mb-5 rounded-xl border border-stone-200 bg-stone-50/70 p-4">
                <summary className="cursor-pointer text-sm font-medium text-stone-700"><Link2 className="mr-2 inline size-4" />从链接导入内容（可选）</summary>
                <div className="mt-3 space-y-3">
                  <div className="flex gap-2">
                    <Input aria-label="提示词来源链接" type="url" value={importUrl} disabled={busy} onChange={(event) => setImportUrl(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void importFromUrl(); } }} placeholder="粘贴网页、Markdown 或 JSON 链接" className="min-w-0 rounded-xl border-stone-200 bg-white" />
                    <Button variant="outline" disabled={busy || !importUrl.trim()} className="shrink-0 rounded-xl border-stone-200 bg-white" onClick={() => void importFromUrl()}>
                      {importing ? <LoaderCircle className="size-4 animate-spin" /> : <Download className="size-4" />}
                      {importing ? "拉取中" : "拉取"}
                    </Button>
                  </div>
                  <p className="text-xs leading-5 text-stone-500">支持公开网页、GitHub 文件及水仙提示词库链接，拉取后可编辑再保存。示例图保留原站链接。</p>
                  {importWarnings.length > 0 ? <div role="status" className="space-y-1 rounded-xl bg-amber-50 p-3 text-xs leading-5 text-amber-800">{importWarnings.map((warning) => <p key={warning}>{warning}</p>)}</div> : null}
                  {importedItems.length > 1 || importResult?.source_name ? (
                    <div className="space-y-2">
                      {importResult?.source_name ? (
                        <>
                          <p className="text-xs text-stone-500">{importResult.source_name} · 共 {importResult.total} 条 · 第 {importResult.page} 页</p>
                          <div className="flex gap-2">
                            <Input aria-label="搜索来源提示词" value={importQuery} disabled={busy} onChange={(event) => setImportQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void importFromUrl({ page: 1, query: importQuery.trim() }); } }} placeholder="搜索标题、分类或提示词正文" className="min-w-0 rounded-xl border-stone-200 bg-white" />
                            <Button variant="outline" disabled={busy} className="shrink-0 rounded-xl border-stone-200 bg-white" onClick={() => void importFromUrl({ page: 1, query: importQuery.trim() })}>搜索</Button>
                          </div>
                        </>
                      ) : <p className="text-xs text-stone-500">识别到 {importedItems.length} 条，选择一条填入下方表单：</p>}
                      <div className="max-h-52 space-y-2 overflow-y-auto">
                        {importedItems.length === 0 ? <p role="status" className="py-3 text-center text-xs text-stone-500">没有匹配的提示词，请更换关键词。</p> : null}
                        {importedItems.map((item, index) => (
                          <button key={index} type="button" disabled={busy} className="flex w-full items-center gap-2 rounded-xl border border-stone-200 p-2 text-left hover:bg-stone-50 disabled:opacity-50" onClick={() => selectDraft(item)}>
                            {item.example_images?.[0] ? <PromptExampleImage src={item.example_images[0]} alt={`${item.name} 示例`} className="size-12" /> : null}
                            <span className="min-w-0"><span className="block truncate text-sm font-medium text-stone-800">{item.name}</span><span className="block text-xs text-stone-500">{item.type}</span><span className="line-clamp-2 text-xs leading-5 text-stone-500">{item.content}</span></span>
                          </button>
                        ))}
                      </div>
                      {importResult?.source_name ? (
                        <div className="flex justify-between gap-2">
                          <Button variant="outline" size="sm" disabled={busy || (importResult.page || 1) <= 1} onClick={() => void importFromUrl({ page: (importResult.page || 1) - 1, query: importResult.query })}>上一页</Button>
                          <Button variant="outline" size="sm" disabled={busy || !importResult.has_more} onClick={() => void importFromUrl({ page: (importResult.page || 1) + 1, query: importResult.query })}>下一页</Button>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </details>
              {form.id && items.find((item) => item.id === form.id)?.subscription_id ? <p className="mb-3 text-xs leading-5 text-amber-700">编辑保存后将保留本地内容，后续订阅同步不会覆盖这条提示词。</p> : null}
              <fieldset disabled={busy} className="min-w-0 space-y-3">
                <Input aria-label="名称" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="名称" className="rounded-xl border-stone-200 bg-white" />
                <Input aria-label="类型" value={form.type} onChange={(event) => setForm({ ...form, type: event.target.value })} placeholder="类型，例如：摄影、人像、产品图" className="rounded-xl border-stone-200 bg-white" />
                <Textarea aria-label="提示词内容" value={form.content} onChange={(event) => setForm({ ...form, content: event.target.value })} placeholder="提示词内容" className="min-h-64 resize-y rounded-xl border-stone-200 bg-white" />
                <Textarea aria-label="备注" value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} placeholder="备注，可选" className="min-h-20 resize-none rounded-xl border-stone-200 bg-white" />
                <Input aria-label="来源 URL" type="url" value={form.source_url} onChange={(event) => setForm({ ...form, source_url: event.target.value })} placeholder="来源 URL，可选" className="rounded-xl border-stone-200 bg-white" />
                <div className="space-y-2">
                  <div className="text-xs font-medium text-stone-700">示例效果图 · {form.example_images.length}/12</div>
                  {form.example_images.length > 0 ? (
                    <div className="grid grid-cols-3 gap-2">
                      {form.example_images.map((url, index) => (
                        <div key={url} className="group relative min-w-0">
                          <button type="button" className="block w-full cursor-zoom-in" aria-label={`预览示例图 ${index + 1}`} onClick={() => openPreview(form.example_images, index)}><PromptExampleImage src={url} alt={`示例效果图 ${index + 1}`} className="aspect-square w-full" /></button>
                          <button type="button" aria-label={`移除示例图 ${index + 1}`} className="absolute right-1 top-1 rounded-full bg-black/60 p-1 text-white hover:bg-black/80" onClick={() => setForm({ ...form, example_images: form.example_images.filter((_, imageIndex) => imageIndex !== index) })}><X className="size-3" /></button>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div className="flex gap-2">
                    <Input aria-label="示例图链接" type="url" value={imageUrl} onChange={(event) => setImageUrl(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addExampleImage(); } }} placeholder="补充图片链接" className="min-w-0 rounded-xl border-stone-200 bg-white" />
                    <Button variant="outline" className="shrink-0 rounded-xl border-stone-200 bg-white" disabled={!imageUrl.trim()} onClick={addExampleImage}>添加</Button>
                  </div>
                </div>
              </fieldset>
            </div>
          ) : null}
        </div>
        <DialogFooter className="shrink-0 border-t border-stone-200 px-5 py-4 sm:px-6">
          {dialogMode === "custom" && !form.id ? <Button variant="ghost" className="sm:mr-auto" disabled={busy} onClick={() => setDialogMode("menu")}><ArrowLeft className="size-4" />返回选择方式</Button> : null}
          <Button variant="secondary" className="rounded-xl" disabled={busy} onClick={closeDialog}>取消</Button>
          {dialogMode === "custom" ? <Button className="rounded-xl bg-stone-950 text-white hover:bg-stone-800" disabled={busy} onClick={() => void save()}>{saving ? <LoaderCircle className="size-4 animate-spin" /> : null}{form.id ? "保存修改" : "保存提示词"}</Button> : null}
        </DialogFooter>
      </DialogContent>

      <PromptSubscriptions open={dialogMode === "subscriptions"} onOpenChange={(open) => { if (!open) setDialogMode(null); }} onBack={() => setDialogMode("menu")} onLibraryChange={refreshLibrary} />
      <ImageLightbox images={previewImages.map((src) => ({ id: src, src }))} currentIndex={previewIndex} open={previewImages.length > 0} onOpenChange={(open) => { if (!open) setPreviewImages([]); }} onIndexChange={setPreviewIndex} />
    </Dialog>
  );
}
