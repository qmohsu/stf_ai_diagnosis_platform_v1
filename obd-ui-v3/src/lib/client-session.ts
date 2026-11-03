export async function clearPortalCaches(): Promise<void> {
  if (!("caches" in window)) return;
  const names = await caches.keys();
  await Promise.allSettled(names.filter((name) => name.includes("sanitized-data")).map((name) => caches.delete(name)));
}

export async function redirectToLogin(): Promise<void> {
  await clearPortalCaches();
  window.location.assign("/login?expired=1");
}
