export function imageMimeType(image: { mime_type?: string; b64_json?: string; url?: string }) {
  if (image.mime_type) return image.mime_type;
  if (image.b64_json?.startsWith("R0lGOD") || /\.gif(?:[?#]|$)/i.test(image.url || "")) return "image/gif";
  return "image/png";
}

export function imageExtension(image: { mime_type?: string; b64_json?: string; url?: string }) {
  const mime = imageMimeType(image);
  return mime === "image/gif" ? "gif" : mime === "image/jpeg" ? "jpg" : mime === "image/webp" ? "webp" : "png";
}
