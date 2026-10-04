/** Copy text; also over plain HTTP from another device, where navigator.clipboard is absent. */
export async function copyText(text: string): Promise<void> {
  if (window.isSecureContext && "clipboard" in navigator) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  try {
    // eslint-disable-next-line @typescript-eslint/no-deprecated -- the only way without HTTPS
    document.execCommand("copy");
  } finally {
    area.remove();
  }
}
