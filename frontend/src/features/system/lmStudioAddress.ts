/** Whether what is typed names this remembered address (the server completes it the same
 * way: no scheme means http; for LM Studio, no port means 1234; for an OpenAI-compatible
 * server, no path means /v1, so « gpu-box:8000 » names http://gpu-box:8000/v1). */
export function names(typed: string, url: string): boolean {
  const bare = (value: string): string =>
    value
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, "")
      .replace(/\/+$/, "");
  const wanted = bare(typed);
  return wanted !== "" && [wanted, `${wanted}:1234`, `${wanted}/v1`].includes(bare(url));
}
