"use client";

import { useState } from "react";
import { ImageOff } from "lucide-react";

import { cn } from "@/lib/utils";

export function PromptExampleImage({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);

  return (
    <span className={cn("flex shrink-0 items-center justify-center overflow-hidden rounded-lg bg-stone-100", className)}>
      {failedSrc === src ? (
        <span className="flex flex-col items-center gap-1 p-2 text-stone-500" title="原站图片暂时无法访问">
          <ImageOff className="size-5" />
          <span className="text-[10px]">图片不可用</span>
        </span>
      ) : (
        <img src={src} alt={alt} loading="lazy" decoding="async" referrerPolicy="no-referrer" className="h-full w-full object-cover" onError={() => setFailedSrc(src)} />
      )}
    </span>
  );
}
