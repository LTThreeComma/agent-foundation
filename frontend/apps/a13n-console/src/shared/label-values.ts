export const validLabelKey = (key: string) =>
  /^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/.test(key);
export const validLabelValue = (value: string) =>
  Array.from(value).length <= 256 && !/[\p{Cc}\p{Cs}]/u.test(value);
