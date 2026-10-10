/** Let the browser download what the application serves at this address, as a link would. */
export function downloadLink(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
}

/** Hand a file received by a request (a POST download) to the browser, as a link would. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  // The browser has read the object once the click is handled: release it after this turn.
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}
