/**
 * Copy text to the clipboard.
 *
 * The async Clipboard API (navigator.clipboard) only exists in a secure
 * context (HTTPS or localhost). This panel is often served over plain HTTP
 * by IP, where navigator.clipboard is undefined — so we fall back to a
 * hidden <textarea> + execCommand("copy"). Returns true on success.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Permission denied or unavailable — fall through to the legacy path.
  }

  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
