/** The page itself: reloaded once the application is back after a restart (the tests replace
 * it). */
export const page = {
  reload(): void {
    window.location.reload();
  },
};
