# Rust Host 技能镜像回归

构建 Rust Host 后运行：

```sh
python3 -B test/2026-11-27_Rust技能工作区同步/host_skill_mirror_http.py --binary backend-rust/target/debug/astrion-headless
```

通过真实的本地 Web 登录、个性化、技能菜单、Agent Task 和回环模拟模型检查：
设置只更新当前工作区；另一注册工作区首次直接发任务也会准备技能；同名个人库优先、附属文件一并复制；关闭保留本地编辑和无关目录；冲突目录给出警告；归档新技能后其它工作区下次访问可见；重启后可以刷新已管理且未改动的副本。不使用用户的真实配置、凭据或提供商。
