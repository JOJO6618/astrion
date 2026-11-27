use super::{json, notice_message};

#[test]
fn background_notice_formats_do_not_change_message_identity() {
    let row = json!({"command_id":"fixture","command":"pwd","return_code":0,
        "status":"completed","output":"workspace\n"});
    let inline = notice_message(&row, 0.0, true, false);
    let idle = notice_message(&row, 0.0, false, false);
    assert_eq!(inline["content"], "[系统通知|background_command]\n[后台 run_command 完成]\nworkspace");
    assert_eq!(idle["content"], "[系统通知|background_command]\n后台指令已完成。\n\n命令：pwd\n退出码：0\n\n输出：\nworkspace\n");
    assert_eq!(inline["message_id"], idle["message_id"]);
    assert_eq!(inline["metadata"], idle["metadata"]);
    assert_eq!(inline["role"], "user");
    assert_eq!(inline["metadata"]["starts_work"], false);
    assert_eq!(inline["metadata"]["message_source"], "background_command");
}

#[test]
fn no_output_or_exit_code_is_invented() {
    let row = json!({"command_id":"fixture","status":"timeout","output":""});
    assert_eq!(notice_message(&row, 0.0, false, true)["content"],
        "[系统通知|background_command]\nBackground commands finished.\n\nOutput:\n[no_output]");
    assert_eq!(notice_message(&row, 0.0, true, true)["content"],
        "[系统通知|background_command]\n[Background run_command finished]\n[no_output]");
}
