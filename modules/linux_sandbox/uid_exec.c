/* Root-owned sandbox entrypoint. Drop to the caller BEFORE executing argv.
 * This file is compiled by the administrator installer; no user code runs here.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <grp.h>
#include <limits.h>
#include <linux/capability.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <fcntl.h>

static unsigned long number(const char *text) {
    char *end = NULL;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (errno || !end || *end || end == text || value > UINT_MAX) {
        fprintf(stderr, "Invalid sandbox identity\n");
        exit(125);
    }
    return value;
}

static void require(int result, const char *operation) {
    if (result < 0) {
        perror(operation);
        exit(125);
    }
}

int main(int argc, char **argv) {
    if (argc < 9 || geteuid() != 0) {
        fprintf(stderr, "Sandbox identity entrypoint requires trusted root setup\n");
        return 125;
    }
    uid_t uid = (uid_t)number(argv[1]);
    gid_t gid = (gid_t)number(argv[2]);
    int lease_fd = open("/.astrion-runtime/lease", O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    require(lease_fd, "sandbox lease descriptor");
    unsigned int mask = (unsigned int)number(argv[3]);
    if (mask > 0777) return 125;
    struct stat lease;
    require(fstat(lease_fd, &lease), "sandbox lease");
    if (!S_ISREG(lease.st_mode) || lease.st_uid != 0 || lease.st_nlink != 1 ||
        (lease.st_mode & 0777) != 0600) {
        fprintf(stderr, "Sandbox lease expired or invalid\n");
        return 125;
    }
    int home_fd = open("/.astrion-runtime/home", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    require(home_fd, "sandbox temporary home");
    struct statfs home_fs;
    require(fstatfs(home_fd, &home_fs), "sandbox temporary filesystem");
    if ((unsigned long)home_fs.f_type != 0x01021994UL) {
        fprintf(stderr, "Sandbox home must be anonymous tmpfs\n");
        return 125;
    }
    require(fchmod(home_fd, 0700), "sandbox temporary home mode");
    require(fchown(home_fd, uid, gid), "sandbox temporary home owner");
    close(home_fd);
    require(prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "no_new_privs");
    require(prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0), "ambient capabilities");
    for (unsigned int capability = 0; capability < 64; capability++) {
        int result = prctl(PR_CAPBSET_DROP, capability, 0, 0, 0);
        if (result < 0 && errno != EINVAL) require(result, "bounding capabilities");
    }
    /* The helper obtains these groups from the pinned peer's kernel identity,
     * never from the request. Preserve ordinary host DAC access semantics. */
    gid_t groups[65536];
    size_t group_count = 0;
    char *group_text = argv[4];
    char *item;
    while ((item = strsep(&group_text, ",")) != NULL) {
        if (!*item) continue;
        if (group_count == 65536) return 125;
        groups[group_count++] = (gid_t)number(item);
    }
    require(setgroups(group_count, groups), "supplementary groups");
    require(setresgid(gid, gid, gid), "sandbox gid");
    require(setresuid(uid, uid, uid), "sandbox uid");
    /* Walk without symlinks AFTER dropping DAC identity. The saved dev/inode
     * detects mount/cwd replacement between source pinning and command start. */
    int cwd_fd = open("/", O_PATH | O_DIRECTORY | O_CLOEXEC);
    require(cwd_fd, "sandbox cwd root");
    char *cwd_text = argv[5];
    while ((item = strsep(&cwd_text, "/")) != NULL) {
        if (!*item) continue;
        int next_fd = openat(cwd_fd, item, O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        require(next_fd, "sandbox cwd component");
        close(cwd_fd);
        cwd_fd = next_fd;
    }
    struct stat cwd_info;
    require(fstat(cwd_fd, &cwd_info), "sandbox cwd identity");
    char *end_dev = NULL, *end_ino = NULL;
    unsigned long long dev = strtoull(argv[6], &end_dev, 10);
    unsigned long long ino = strtoull(argv[7], &end_ino, 10);
    if (!end_dev || *end_dev || !end_ino || *end_ino ||
        (unsigned long long)cwd_info.st_dev != dev || (unsigned long long)cwd_info.st_ino != ino) {
        fprintf(stderr, "Sandbox working directory changed during setup\n");
        return 125;
    }
    require(fchdir(cwd_fd), "sandbox working directory");
    close(cwd_fd);
    struct __user_cap_header_struct header = {
        .version = _LINUX_CAPABILITY_VERSION_3, .pid = 0
    };
    struct __user_cap_data_struct capabilities[2] = {{0}, {0}};
    require(syscall(SYS_capset, &header, &capabilities), "clear capabilities");
    umask(mask);
    /* All mount/source/filter/control descriptors stop at this boundary. */
#ifdef SYS_close_range
    if (syscall(SYS_close_range, 3U, UINT_MAX, 0U) < 0)
#endif
    {
        struct rlimit limit;
        require(getrlimit(RLIMIT_NOFILE, &limit), "descriptor limit");
        unsigned long maximum = limit.rlim_cur == RLIM_INFINITY ? 1048576 : limit.rlim_cur;
        for (unsigned long fd = 3; fd < maximum; fd++) close((int)fd);
    }
    execvp(argv[8], argv + 8);
    perror("sandbox exec");
    return 126;
}
