use super::*;

#[test]
fn resize_uses_png_and_keeps_aspect_ratio() {
    let mut input = Cursor::new(Vec::new());
    image::DynamicImage::new_rgb8(2400, 1200).write_to(&mut input, ImageFormat::Jpeg).unwrap();
    let bytes = input.into_inner();
    for (bounds, expected) in [((1920, 1080), (1920, 960)), ((1280, 720), (1280, 640)), ((960, 540), (960, 480))] {
        let result = resize(&bytes, bounds).unwrap();
        assert_eq!(image::guess_format(&result).unwrap(), ImageFormat::Png);
        assert_eq!(image::load_from_memory(&result).unwrap().into_rgb8().dimensions(), expected);
    }
    assert!(resize(&bytes, (2400, 1200)).is_none());
    assert!(resize(b"not an image", (960, 540)).is_none());
}

#[test]
fn only_resizes_the_model_copy_of_a_workspace_path_image() {
    let root = std::env::temp_dir().join(format!("astrion-path-image-{}", uuid::Uuid::new_v4()));
    let workspace = root.join("workspace");
    let data = root.join("data");
    std::fs::create_dir_all(&workspace).unwrap();
    std::fs::create_dir_all(&data).unwrap();
    let file = workspace.join("fixture.jpg");
    image::DynamicImage::new_rgb8(1200, 900).save(&file).unwrap();
    let original = std::fs::read(&file).unwrap();
    let user = serde_json::json!({"role":"user","content":"fixture"});
    let document = serde_json::json!({"messages":[{"role":"user","content":"fixture","images":["fixture.jpg"]}]});
    let attach = |setting: &str| {
        std::fs::write(data.join("personalization.json"),
            serde_json::json!({"image_compression":setting}).to_string()).unwrap();
        let mut model = [user.clone()];
        super::super::attach(&document, &mut model, &data, &workspace, &workspace);
        model[0]["content"].as_array().unwrap().iter()
            .find_map(|part| part["image_url"]["url"].as_str()).unwrap().to_owned()
    };
    let original_wire = attach("original");
    let resized_wire = attach("540p");
    assert!(original_wire.starts_with("data:image/jpeg;base64,"));
    assert!(resized_wire.starts_with("data:image/png;base64,"));
    use base64::{Engine, engine::general_purpose::STANDARD};
    let converted = STANDARD.decode(resized_wire.split_once(',').unwrap().1).unwrap();
    assert_eq!(image::load_from_memory(&converted).unwrap().into_rgb8().dimensions(), (720, 540));
    assert_eq!(std::fs::read(&file).unwrap(), original);
    std::fs::remove_dir_all(root).unwrap();
}
