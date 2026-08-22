# utils/terminal_factory.py - 跨平台终端工厂（修改为Windows优先使用CMD）

import sys
import os
import subprocess
import shutil
from typing import Optional, Dict, List
from pathlib import Path

class TerminalFactory:
    """跨平台终端工厂，用于创建合适的终端进程"""
    
    def __init__(self):
        """初始化终端工厂"""
        self.platform = sys.platform
        self.available_shells = self._detect_available_shells()
    
    def _detect_available_shells(self) -> Dict[str, str]:
        """检测系统中可用的shell"""
        shells = {}
        
        if self.platform == "win32":
            # Windows系统
            # 检查cmd（优先）
            if shutil.which("cmd.exe"):
                shells["cmd"] = "cmd.exe"
            
            # 检查PowerShell（备用）
            if shutil.which("powershell.exe"):
                shells["powershell"] = "powershell.exe"
            
            # 检查Windows Terminal（新版Windows）
            if shutil.which("wt.exe"):
                shells["wt"] = "wt.exe"
            
            # 检查Git Bash
            git_bash_paths = [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files (x86)\Git\bin\bash.exe",
                os.path.expanduser("~/AppData/Local/Programs/Git/bin/bash.exe")
            ]
            for path in git_bash_paths:
                if os.path.exists(path):
                    shells["git-bash"] = path
                    break
            
            # 检查WSL
            if shutil.which("wsl.exe"):
                shells["wsl"] = "wsl.exe"
                
        else:
            # Unix-like系统（Linux, macOS）
            # 检查bash
            if shutil.which("bash"):
                shells["bash"] = "/bin/bash"
            
            # 检查zsh（macOS默认）
            if shutil.which("zsh"):
                shells["zsh"] = "/bin/zsh"
            
            # 检查sh
            if shutil.which("sh"):
                shells["sh"] = "/bin/sh"
            
            # 检查fish
            if shutil.which("fish"):
                shells["fish"] = shutil.which("fish")
        
        return shells
    
    def get_shell_command(self, preferred: Optional[str] = None) -> str:
        """
        获取合适的shell命令
        
        Args:
            preferred: 首选的shell类型
            
        Returns:
            shell命令路径
        """
        # 如果指定了首选shell且可用
        if preferred and preferred in self.available_shells:
            return self.available_shells[preferred]
        
        # 根据平台选择默认shell
        if self.platform == "win32":
            # Windows优先级：CMD优先！（修改这里）
            if "cmd" in self.available_shells:
                return self.available_shells["cmd"]
            elif "powershell" in self.available_shells:
                return self.available_shells["powershell"]
            elif "git-bash" in self.available_shells:
                return self.available_shells["git-bash"]
            else:
                # 最后的默认选项
                return "cmd.exe"
                
        elif self.platform == "darwin":
            # macOS优先级：zsh (默认) > bash > sh
            if "zsh" in self.available_shells:
                return self.available_shells["zsh"]
            elif "bash" in self.available_shells:
                return self.available_shells["bash"]
            else:
                return "/bin/sh"
                
        else:
            # Linux优先级：bash > zsh > sh
            if "bash" in self.available_shells:
                return self.available_shells["bash"]
            elif "zsh" in self.available_shells:
                return self.available_shells["zsh"]
            else:
                return "/bin/sh"
    
    def get_clear_command(self) -> str:
        """获取清屏命令"""
        if self.platform == "win32":
            return "cls"
        else:
            return "clear"
    
    def get_list_command(self) -> str:
        """获取列出文件命令"""
        if self.platform == "win32":
            return "dir"
        else:
            return "ls -la"
    
    def get_change_dir_command(self, path: str) -> str:
        """获取切换目录命令"""
        return f"cd {path}"
    
    def get_python_command(self) -> str:
        """获取Python命令"""
        # Windows优先顺序调整
        if self.platform == "win32":
            # Windows: 优先python，然后py，最后python3
            if shutil.which("python"):
                return "python"
            elif shutil.which("py"):
                return "py"
            elif shutil.which("python3"):
                return "python3"
            else:
                return "python"
        else:
            # Unix-like: 优先python3
            if shutil.which("python3"):
                return "python3"
            elif shutil.which("python"):
                return "python"
            else:
                return "python3"
    
    def get_pip_command(self) -> str:
        """获取pip命令"""
        python_cmd = self.get_python_command()
        return f"{python_cmd} -m pip"
    
    def get_env_activation_command(self, venv_path: str) -> str:
        """
        获取虚拟环境激活命令
        
        Args:
            venv_path: 虚拟环境路径
            
        Returns:
            激活命令
        """
        venv_path = Path(venv_path)
        
        if self.platform == "win32":
            # Windows
            activate_script = venv_path / "Scripts" / "activate.bat"
            if activate_script.exists():
                return str(activate_script)
            
            # PowerShell脚本（备用）
            ps_script = venv_path / "Scripts" / "Activate.ps1"
            if ps_script.exists():
                return f"& '{ps_script}'"
                
        else:
            # Unix-like
            activate_script = venv_path / "bin" / "activate"
            if activate_script.exists():
                return f"source {activate_script}"
        
        return ""
    
    def format_command_with_timeout(self, command: str, timeout_seconds: int) -> str:
        """
        格式化带超时的命令
        
        Args:
            command: 原始命令
            timeout_seconds: 超时秒数
            
        Returns:
            带超时的命令
        """
        if self.platform == "win32":
            # Windows没有内置的timeout命令用于限制其他命令
            # 需要使用PowerShell或其他方法
            return command
        else:
            # Unix-like系统使用timeout命令
            return f"timeout {timeout_seconds} {command}"
    
    def get_process_list_command(self) -> str:
        """获取进程列表命令"""
        if self.platform == "win32":
            return "tasklist"
        elif self.platform == "darwin":
            return "ps aux"
        else:
            return "ps aux"
    
    def get_kill_command(self, process_id: int) -> str:
        """
        获取终止进程命令
        
        Args:
            process_id: 进程ID
            
        Returns:
            终止命令
        """
        if self.platform == "win32":
            return f"taskkill /PID {process_id} /F"
        else:
            return f"kill -9 {process_id}"
    
    def get_system_info(self) -> Dict:
        """获取系统信息"""
        info = {
            "platform": self.platform,
            "platform_name": self._get_platform_name(),
            "available_shells": list(self.available_shells.keys()),
            "default_shell": self.get_shell_command(),
            "python_command": self.get_python_command(),
            "pip_command": self.get_pip_command()
        }
        
        # 添加系统版本信息
        try:
            import platform
            info["system"] = platform.system()
            info["release"] = platform.release()
            info["version"] = platform.version()
            info["machine"] = platform.machine()
            info["processor"] = platform.processor()
        except:
            pass
        
        return info
    
    def _get_platform_name(self) -> str:
        """获取友好的平台名称"""
        if self.platform == "win32":
            return "Windows"
        elif self.platform == "darwin":
            return "macOS"
        elif self.platform.startswith("linux"):
            return "Linux"
        else:
            return "Unknown"
    
    def create_terminal_config(self, working_dir: str = None) -> Dict:
        """
        创建终端配置
        
        Args:
            working_dir: 工作目录
            
        Returns:
            终端配置字典
        """
        config = {
            "shell": self.get_shell_command(),  # 这里会使用cmd.exe
            "working_dir": working_dir or os.getcwd(),
            "env": os.environ.copy(),
            "platform": self.platform
        }
        
        # Windows特殊配置
        if self.platform == "win32":
            # 设置代码页为UTF-8
            config["env"]["PYTHONIOENCODING"] = "utf-8"
            config["startup_commands"] = ["chcp 65001"]  # UTF-8代码页
        else:
            # Unix-like特殊配置
            config["env"]["TERM"] = "xterm-256color"
            config["startup_commands"] = []
        
        return config
    
    def test_shell(self, shell_path: str) -> bool:
        """
        测试shell是否可用
        
        Args:
            shell_path: shell路径
            
        Returns:
            是否可用
        """
        try:
            # 尝试运行一个简单命令
            result = subprocess.run(
                [shell_path, "/c" if self.platform == "win32" else "-c", "echo test"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            return False