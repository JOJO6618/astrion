//! Resize only workspace-path images in the model's projection. The source
//! file, blob-backed copies, download API and Chat media are not modified.

use std::{io::Cursor, path::Path};

use image::{ImageFormat, ImageReader, Limits};

pub(super) fn bounds(data: &Path) -> Option<(u32, u32)> {
    crate::web_assets::personalization::agent_path_image_bounds(data)
}

pub(super) fn resize(bytes: &[u8], (width, height): (u32, u32)) -> Option<Vec<u8>> {
    let mut reader = ImageReader::new(Cursor::new(bytes)).with_guessed_format().ok()?;
    let format = reader.format()?;
    // Preserve animation and formats the Rust binary may not decode.
    if !matches!(format, ImageFormat::Jpeg | ImageFormat::Png | ImageFormat::Bmp) {
        return None;
    }
    let mut limits = Limits::default();
    limits.max_image_width = Some(16_384);
    limits.max_image_height = Some(16_384);
    limits.max_alloc = Some(128 * 1024 * 1024);
    if format == ImageFormat::Png
        && image::codecs::png::PngDecoder::with_limits(Cursor::new(bytes), limits.clone())
            .ok()?.is_apng().ok()?
    {
        return None;
    }
    reader.limits(limits.clone());
    let (actual_width, actual_height) = reader.into_dimensions().ok()?;
    if actual_width <= width && actual_height <= height
        || u64::from(actual_width) * u64::from(actual_height) > 16_777_216
    {
        return None;
    }
    let mut reader = ImageReader::with_format(Cursor::new(bytes), format);
    reader.limits(limits);
    let image = reader.decode().ok()?.thumbnail(width, height);
    let mut output = Cursor::new(Vec::new());
    image.write_to(&mut output, ImageFormat::Png).ok()?;
    Some(output.into_inner())
}

#[cfg(test)]
#[path = "../../../../test/2026-11-27_RustAgent设置生效/path_images_contract.rs"]
mod contract;
