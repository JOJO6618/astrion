use serde_json::{Value, json};

pub(crate) const NAME: &str = "trigger_easter_egg";

pub(crate) fn definition() -> Value {
    json!({"type":"function","function":{"name":NAME,"description":"触发彩蛋特效，可选灌水或贪吃蛇。",
        "parameters":{"type":"object","properties":{"effect":{"type":"string"},"intent":{"type":"string"}},
        "required":["effect"]}}})
}

pub(crate) fn execute(args: &Value, timestamp: f64) -> Value {
    let effect = args["effect"]
        .as_str()
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    match effect.as_str() {
        "flood" | "water" | "wave" | "灌水" | "注水" => json!({
            "success":true,"effect":"flood","display_name":"灌水","duration_seconds":45,
            "intensity_range":[0.87,0.93],"effect_created_at":timestamp,"message":"已触发灌水彩蛋。"
        }),
        "snake" | "贪吃蛇" | "snakegame" | "snake_game" => json!({
            "success":true,"effect":"snake","display_name":"贪吃蛇","duration_seconds":200,
            "apples_target":20,"initial_apples":3,"effect_created_at":timestamp,"message":"已触发贪吃蛇彩蛋。"
        }),
        _ => {
            json!({"success":false,"error":"请选择支持的彩蛋效果。","available_effects":["flood","snake"]})
        }
    }
}
