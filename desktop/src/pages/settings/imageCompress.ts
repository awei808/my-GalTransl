/**
 * Image compression utility — compresses user-selected background images
 * to a JPEG data URL via canvas to fit within localStorage quota.
 */

const CUSTOM_BACKGROUND_MAX_EDGE = 1920;
const CUSTOM_BACKGROUND_JPEG_QUALITY = 0.82;

export async function compressImageToDataUrl(file: File): Promise<string> {
  const objectUrl = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("无法读取图片文件。"));
      image.src = objectUrl;
    });

    const scale = Math.min(
      1,
      CUSTOM_BACKGROUND_MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight),
    );
    const targetWidth = Math.max(1, Math.round(img.naturalWidth * scale));
    const targetHeight = Math.max(1, Math.round(img.naturalHeight * scale));

    const canvas = document.createElement("canvas");
    canvas.width = targetWidth;
    canvas.height = targetHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      throw new Error("当前环境不支持 canvas 压缩。");
    }
    ctx.drawImage(img, 0, 0, targetWidth, targetHeight);

    return canvas.toDataURL("image/jpeg", CUSTOM_BACKGROUND_JPEG_QUALITY);
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
