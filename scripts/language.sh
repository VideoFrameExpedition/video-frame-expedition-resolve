# Sourced by run.command and scripts/bootstrap.sh, from the application's folder: the language of
# the messages on screen, French or English. VFE_LANG=fr or en (the environment, then the .env
# file) decides; otherwise the Mac's first preferred language (System Settings › General ›
# Language & Region), then the locale. Anything other than French gives English. Exported, so
# that the application's commands started next speak the same language.

if [ -z "${VFE_LANG:-}" ] && [ -f .env ]; then
  VFE_LANG=$(sed -n 's/^[[:space:]]*VFE_LANG[[:space:]]*=//p' .env | tail -n 1 | tr -d "\"' \r")
fi
if [ -z "${VFE_LANG:-}" ] && command -v defaults >/dev/null 2>&1; then
  # A list, one code per line after the opening parenthesis: ("fr-FR", "en-FR")
  VFE_LANG=$(defaults read -g AppleLanguages 2>/dev/null | sed -n '2p' | tr -d "\" ,")
fi
case "${VFE_LANG:-${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}}" in
  fr* | FR*) VFE_LANG=fr ;;
  *) VFE_LANG=en ;;
esac
export VFE_LANG

# say <French> <English>: one line in the language of the messages.
say() {
  if [ "$VFE_LANG" = fr ]; then
    printf '%s\n' "$1"
  else
    printf '%s\n' "$2"
  fi
}
