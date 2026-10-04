/** Whether what is typed names this remembered address (the server completes it the same
 * way: no scheme means http, no port means 1234). */
export function names(typed: string, url: string): boolean {
  const bare = (value: string): string =>
    value
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, "")
      .replace(/\/+$/, "");
  const wanted = bare(typed);
  return wanted !== "" && (bare(url) === wanted || bare(url) === `${wanted}:1234`);
}
