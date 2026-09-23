/** A model key the Service accepts: lowercase letters, digits, `_` and `-`. */
export function suggestedKey(value: string) {
  return value
    .normalize("NFC")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .slice(0, 128)
    .replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, "");
}
