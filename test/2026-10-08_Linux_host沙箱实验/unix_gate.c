/* Experimental cgroup hook: deny UNIX connect/sendmsg in OUR transient unit.
 * AF_UNIX socketpair remains usable. This intentionally blocks filesystem and
 * abstract UNIX network endpoints; compatibility is an explicit PoC tradeoff.
 * No pinning, global cgroup attachment, or persistent system configuration.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/bpf.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

static int bpf_call(enum bpf_cmd command, union bpf_attr *attributes) {
    return syscall(SYS_bpf, command, attributes, sizeof(*attributes));
}

static int deny_endpoint(int cgroup_fd, enum bpf_attach_type type) {
    struct bpf_insn instructions[] = {
        {.code = BPF_ALU64 | BPF_MOV | BPF_K, .dst_reg = BPF_REG_0, .imm = 0},
        {.code = BPF_JMP | BPF_EXIT},
    };
    char log[16384] = {0};
    char license[] = "GPL";
    union bpf_attr load = {0};
    load.prog_type = BPF_PROG_TYPE_CGROUP_SOCK_ADDR;
    load.expected_attach_type = type;
    load.insn_cnt = sizeof(instructions) / sizeof(instructions[0]);
    load.insns = (unsigned long)instructions;
    load.license = (unsigned long)license;
    load.log_buf = (unsigned long)log;
    load.log_size = sizeof(log);
    load.log_level = 1;
    snprintf(load.prog_name, sizeof(load.prog_name), "astrion_unix");
    int program_fd = bpf_call(BPF_PROG_LOAD, &load);
    if (program_fd < 0) {
        fprintf(stderr, "BPF load failed: %s %s\n", strerror(errno), log);
        return -1;
    }
    union bpf_attr attach = {0};
    attach.target_fd = cgroup_fd;
    attach.attach_bpf_fd = program_fd;
    attach.attach_type = type;
    attach.attach_flags = BPF_F_ALLOW_MULTI;
    int result = bpf_call(BPF_PROG_ATTACH, &attach);
    close(program_fd);
    if (result < 0) fprintf(stderr, "BPF attach failed: %s\n", strerror(errno));
    return result;
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    FILE *input = fopen("/proc/self/cgroup", "r");
    if (!input) return 2;
    char line[1024], relative[1024] = {0};
    while (fgets(line, sizeof(line), input)) {
        if (strncmp(line, "0::/system.slice/astrion-sandbox-", 31) == 0) {
            snprintf(relative, sizeof(relative), "%s", line + 3);
            relative[strcspn(relative, "\n")] = 0;
            break;
        }
    }
    fclose(input);
    /* Refuse root/parent cgroups: only one specifically named transient unit. */
    const char *prefix = "/system.slice/astrion-sandbox-";
    if (strncmp(relative, prefix, strlen(prefix)) != 0 ||
        strchr(relative + strlen(prefix), '/') != NULL ||
        strstr(relative, ".service") == NULL) {
        fprintf(stderr, "Refusing attachment outside experimental unit\n");
        return 2;
    }
    char path[1200];
    snprintf(path, sizeof(path), "/sys/fs/cgroup%s", relative);
    int cgroup_fd = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (cgroup_fd < 0) return 2;
    if (deny_endpoint(cgroup_fd, BPF_CGROUP_UNIX_CONNECT) < 0 ||
        deny_endpoint(cgroup_fd, BPF_CGROUP_UNIX_SENDMSG) < 0) {
        close(cgroup_fd);
        return 2;
    }
    close(cgroup_fd);
    execvp(argv[1], argv + 1);
    perror("execvp");
    return 2;
}
