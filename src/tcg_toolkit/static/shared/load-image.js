// COS signs GETs with a start and end time in Unix seconds.
export function expiredImageSignature(url) {
  const period = new URL(url).searchParams.get('q-sign-time');
  return Boolean(period) && Number(period.split(';')[1]) <= Date.now() / 1000;
}

// Decode a picked file; browsers apply EXIF orientation to <img> and to drawImage().
// load/error rather than decode(): Chromium can leave decode() pending in a page that is not painting.
export async function loadImage(file) {
  const url = URL.createObjectURL(file), image = new Image();
  try {
    await new Promise((resolve, reject) => {
      image.addEventListener('load', resolve, {once: true});
      image.addEventListener('error', reject, {once: true});
      image.src = url;
    });
    if (!image.naturalWidth || !image.naturalHeight) throw new Error('empty image');
    return image;
  } finally { URL.revokeObjectURL(url); }
}
