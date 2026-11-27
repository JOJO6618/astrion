use super::*;

#[test]
fn native_workspace_path_wins_only_when_inside_the_registered_root() {
    let root = Path::new("/workspace/registered");
    let relative = |input: &str| {
        native_or_workspace_relative(Path::new(input), input, root, root).map(Path::to_path_buf)
    };
    assert_eq!(
        relative("/workspace/registered/fixtures/test.txt").unwrap(),
        Path::new("fixtures/test.txt"),
    );
    assert_eq!(
        relative("/workspace/fixtures/test.txt").unwrap(),
        Path::new("fixtures/test.txt"),
    );
    assert_eq!(relative("fixtures/test.txt").unwrap(), Path::new("fixtures/test.txt"));
    assert!(relative("/unrelated/fixtures/test.txt").is_err());
}
