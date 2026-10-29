"use client";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useLocale } from "@/components/locale-provider";
import { cn } from "@/lib/utils";
export function Sheet({ open, onOpenChange, title, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; children: React.ReactNode }) {
  const { messages } = useLocale();
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className={cn("sheet-overlay")} /><Dialog.Content className={cn("sheet-content")} aria-describedby={undefined}><div className="sheet-head"><Dialog.Title>{title}</Dialog.Title><Dialog.Close className="icon-button" aria-label={messages.nav.closeTree}><X aria-hidden="true" /></Dialog.Close></div>{children}</Dialog.Content></Dialog.Portal></Dialog.Root>;
}
