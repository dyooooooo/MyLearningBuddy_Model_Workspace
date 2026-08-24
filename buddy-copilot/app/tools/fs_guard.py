"""文件改动守护：白名单校验、改动检测、回滚。

Cursor Agent 在本地运行时对工作区有完整文件权限，规则只是软约束；
这里做硬校验：Agent 跑完后用 git 对比改动，白名单外的修改一律拦下并
提示用户处理，白名单内的修改支持一键回滚（执行失败/验收不过时）。
"""

from __future__ import annotations

import filecmp
import shutil
import subprocess
import tempfile
from pathlib import Path

# 兜底默认值 —— 正式来源是 agent-env/permissions.yaml 的 files 段（数据驱动）
WRITE_WHITELIST = (
    "learning-buddy-api/configs/editions/",       # 方案种子/资产（bind-mount，便宜）
    "learning-buddy-api/configs/checks/",         # 跨书症状检测器（bind-mount，便宜）
    "learning-buddy-api/data/prompts/",           # MinerU 线 prompt 模板（打进镜像，昂贵）
    "learning-buddy-api/app/import_35b/prompts/", # 35B 线 prompt 模板（打进镜像，昂贵）
    "learning-buddy-api/tests/",                  # 测试
    "learning-buddy-api/app/static/",             # 教材渲染 CSS 等前台静态资源
)

# 永远禁止触碰（兜底默认值，同上）
FORBIDDEN = (
    ".env",
    "learning-buddy-c2/",
)

# 白名单内的「专属闸」子路径：落在白名单目录里，但**不由目录级前缀自动放行**。
#
# 隐患根因：is_whitelisted 与 covered_by_approval 都是前缀匹配，所以
# configs/checks/ 一进白名单，configs/checks/vocab_ext/ 就自动继承了。而那个目录
# 里是**会在 buddy-worker 进程里执行的受限 Python**，不是声明文件——等于默认允许
# Agent 写可执行代码。必须显式拆出来，走独立的批准动作。
GATED_SUBPATHS = (
    "learning-buddy-api/configs/checks/vocab_ext/",
)

# 专属闸内的例外：加载器只扫 vocab_ext/ 顶层，proposals/ 里的文件永远不会被执行，
# 所以草稿落在这里和写一份 YAML 一样安全，普通 fs.request_edit 即可。
GATE_EXEMPT_SUBPATHS = (
    "learning-buddy-api/configs/checks/vocab_ext/proposals/",
)

# 白名单外但要能「真回滚」的源码区（兜底默认值，同上）
GUARDED_ROOTS = (
    "learning-buddy-api/app/",
    "exam-ocr-server/app/",
    "buddy-copilot/app/",
)

# 只备份源码后缀：guarded_roots 里混着几十 MB 资产，整树复制不可接受
BACKUP_SUFFIXES = (".py", ".md", ".json", ".yaml", ".yml", ".css", ".html")

# 前台显示资产后缀（兜底；正式来源 permissions.yaml files.auto_allow_suffixes）
AUTO_ALLOW_SUFFIXES = (".css", ".html")


def gated_prefix(
    rel_path: str,
    gated_subpaths: tuple[str, ...] = GATED_SUBPATHS,
    exempt_subpaths: tuple[str, ...] = GATE_EXEMPT_SUBPATHS,
) -> str:
    """该路径落在哪个专属闸子路径下；不在任何一个则返回空串。"""
    p = rel_path.replace("\\", "/")
    if any(p.startswith(e) for e in exempt_subpaths):
        return ""
    for g in gated_subpaths:
        if p.startswith(g):
            return g
    return ""


def is_whitelisted(
    rel_path: str,
    whitelist: tuple[str, ...] = WRITE_WHITELIST,
    forbidden: tuple[str, ...] = FORBIDDEN,
    gated_subpaths: tuple[str, ...] = GATED_SUBPATHS,
    *,
    allow_gated: bool = False,
) -> bool:
    """该路径是否可写。

    ``allow_gated`` 只有两处该开：专属闸自己的申请工具，以及运行后统计改动面的
    ``changed_since``（否则专属闸内的文件会被当成「白名单外越权」直接回滚，
    批准过也写不进去）。其余调用方一律用默认值。
    """
    p = rel_path.replace("\\", "/")
    for f in forbidden:
        if p == f or p.startswith(f) or p.endswith("/" + f) or p.endswith(f):
            return False
    if not allow_gated and gated_prefix(p, gated_subpaths):
        return False
    return any(p.startswith(w) for w in whitelist)


def covered_by_approval(
    rel_path: str,
    approved_paths: tuple[str, ...] | list[str],
    gated_subpaths: tuple[str, ...] = GATED_SUBPATHS,
) -> bool:
    """该文件是否落在某次批准的路径范围内。

    批准是「按路径」的，不是「按会话」的：批了一个 prompt 模板不等于
    整个白名单开闸。目录形式（以 / 结尾）按前缀匹配，文件按精确匹配。

    专属闸另加一条：**上级目录的批准不覆盖专属闸目录**。批了
    ``configs/checks/`` 不等于批了 ``configs/checks/vocab_ext/`` 里的可执行代码，
    后者必须由落在它自己前缀下的那次批准来覆盖。
    """
    p = rel_path.replace("\\", "/")
    gate = gated_prefix(p, gated_subpaths)
    for raw in approved_paths:
        a = str(raw).replace("\\", "/").strip()
        if not a:
            continue
        if gate and not a.startswith(gate):
            continue
        if a.endswith("/"):
            if p.startswith(a):
                return True
        elif p == a:
            return True
    return False


def is_auto_allowed(
    rel_path: str,
    whitelist: tuple[str, ...] = WRITE_WHITELIST,
    forbidden: tuple[str, ...] = FORBIDDEN,
    auto_allow_suffixes: tuple[str, ...] = AUTO_ALLOW_SUFFIXES,
    gated_subpaths: tuple[str, ...] = GATED_SUBPATHS,
) -> bool:
    """白名单内的前台显示资产（CSS/HTML）：免 fs.request_edit，仍受白名单约束。

    专属闸子路径永远不免闸门——这里默认 ``allow_gated=False``，所以哪怕将来
    往 auto_allow_suffixes 里加了 ``.py``，vocab_ext/ 也不会被自动放行。
    """
    if not auto_allow_suffixes:
        return False
    if not is_whitelisted(rel_path, whitelist, forbidden, gated_subpaths):
        return False
    p = rel_path.replace("\\", "/")
    return any(p.endswith(s) for s in auto_allow_suffixes)


class WorktreeSnapshot:
    """Agent 运行前的工作区状态快照（基于 git）。白名单/禁区由调用方注入（permissions.yaml）。"""

    def __init__(
        self,
        workspace: Path,
        whitelist: tuple[str, ...] = WRITE_WHITELIST,
        forbidden: tuple[str, ...] = FORBIDDEN,
        guarded_roots: tuple[str, ...] = GUARDED_ROOTS,
        backup_suffixes: tuple[str, ...] = BACKUP_SUFFIXES,
        gated_subpaths: tuple[str, ...] = GATED_SUBPATHS,
    ) -> None:
        self.workspace = workspace
        self.whitelist = whitelist
        self.forbidden = forbidden
        self.guarded_roots = guarded_roots
        self.backup_suffixes = backup_suffixes
        self.gated_subpaths = gated_subpaths
        self.before: dict[str, str] = self._dirty_hashes()
        self._backup_dir: Path | None = None

    def _git(self, *args: str) -> str:
        out = subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return out.stdout

    def _dirty_files(self) -> list[str]:
        lines = self._git("status", "--porcelain").splitlines()
        files = []
        for ln in lines:
            if not ln.strip():
                continue
            path = ln[3:].strip().strip('"')
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.append(path)
        return files

    def _dirty_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for f in self._dirty_files():
            fp = self.workspace / f
            if fp.is_file():
                h = self._git("hash-object", f).strip()
                hashes[f] = h
            else:
                hashes[f] = "<deleted>"
        return hashes

    def backup_guarded(self) -> None:
        """执行前备份：白名单整树 + guarded_roots 的源码文件。

        备份 guarded_roots 是为了让「越权改动」真能回滚——git 只帮得上已跟踪
        文件，未跟踪文件（新增或从未入库的源码）没有基线可还原。
        """
        self._backup_dir = Path(tempfile.mkdtemp(prefix="copilot-fsbak-"))
        for w in self.whitelist:
            src = self.workspace / w
            if src.exists():
                dst = self._backup_dir / w
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, dst, dirs_exist_ok=True)
        for g in self.guarded_roots:
            root = self.workspace / g
            if not root.is_dir():
                continue
            for src in root.rglob("*"):
                if not src.is_file() or src.suffix not in self.backup_suffixes:
                    continue
                dst = self._backup_dir / g / src.relative_to(root)
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    continue  # 容器 root 属主等读不到的文件跳过，不阻塞会话

    # 旧名保留：调用方语义未变（执行前建立可回滚基线）
    backup_whitelist = backup_guarded

    def _backup_path(self, rel_path: str) -> Path | None:
        if self._backup_dir is None:
            return None
        p = self._backup_dir / rel_path
        return p if p.is_file() else None

    def rollback_all_violations(self, files: list[str]) -> tuple[list[str], list[str]]:
        """按备份字节还原越权改动；备份里没有的文件视为本次新增 → 删除。

        与 :meth:`rollback_whitelist` 的区别：这里按「具体越权文件清单」还原，
        覆盖白名单外的 guarded_roots，且如实报告哪些没能还原。
        """
        restored: list[str] = []
        failed: list[str] = []
        for rel in files:
            live = self.workspace / rel
            bak = self._backup_path(rel)
            try:
                if bak is not None:
                    if live.is_file() and filecmp.cmp(bak, live, shallow=False):
                        continue
                    live.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(bak, live)
                    restored.append(rel)
                elif live.is_file():
                    if not self._is_backed_up_area(rel):
                        failed.append(f"{rel}: 不在备份范围内，未回滚")
                        continue
                    live.unlink()
                    restored.append(f"{rel}（本次新增，已删除）")
            except OSError as e:
                failed.append(f"{rel}: {e}")
        return restored, failed

    def _is_backed_up_area(self, rel_path: str) -> bool:
        """该路径是否落在备份覆盖范围（白名单或 guarded_roots 的源码后缀）。"""
        p = rel_path.replace("\\", "/")
        if any(p.startswith(w) for w in self.whitelist):
            return True
        return any(p.startswith(g) for g in self.guarded_roots) and p.endswith(
            self.backup_suffixes
        )

    def changed_since(self) -> tuple[list[str], list[str]]:
        """返回 (白名单内改动, 白名单外改动)。只统计本次新增/内容变化的文件。

        专属闸内的文件算「白名单内」（``allow_gated=True``）——它们照样要过批准，
        只是那道批准由 ``covered_by_approval`` 按专属前缀卡，不在这里当越权回滚。
        """
        after = self._dirty_hashes()
        changed = [
            f for f, h in after.items() if self.before.get(f) != h
        ]

        def writable(rel: str) -> bool:
            return is_whitelisted(
                rel, self.whitelist, self.forbidden, self.gated_subpaths, allow_gated=True
            )

        ok = [f for f in changed if writable(f)]
        bad = [f for f in changed if not writable(f)]
        return ok, bad

    def rollback_whitelist(self) -> tuple[list[str], list[str]]:
        """把白名单目录恢复到 backup_whitelist() 时的状态。

        按文件精确回滚（只动内容有差异/新增的文件），不整树 rmtree——
        目录里可能混有容器（root）写入的文件，宿主机用户无权删除，
        整树删除会直接 PermissionError 炸掉会话。
        返回 (成功恢复的文件, 失败明细)。
        """
        if self._backup_dir is None:
            return [], []
        restored: list[str] = []
        failed: list[str] = []
        for w in self.whitelist:
            bak_root = self._backup_dir / w
            live_root = self.workspace / w
            if not bak_root.exists():
                continue  # 备份时目录不存在，跳过（不猜测新建目录归属）
            # 1) 恢复被修改/被删除的文件
            for bf in bak_root.rglob("*"):
                if not bf.is_file():
                    continue
                rel = bf.relative_to(bak_root)
                lf = live_root / rel
                try:
                    if lf.is_file() and filecmp.cmp(bf, lf, shallow=False):
                        continue  # 内容未变，不碰（避免触碰 root 属主文件）
                    lf.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(bf, lf)
                    restored.append(f"{w}{rel}")
                except OSError as e:
                    failed.append(f"{w}{rel}: {e}")
            # 2) 删除本次新增的文件
            if live_root.exists():
                for lf in live_root.rglob("*"):
                    if not lf.is_file():
                        continue
                    rel = lf.relative_to(live_root)
                    if (bak_root / rel).exists():
                        continue
                    try:
                        lf.unlink()
                        restored.append(f"{w}{rel} (新增已删)")
                    except OSError as e:
                        failed.append(f"{w}{rel}: {e}")
        return restored, failed

    def diff_stat(self, files: list[str] | None = None) -> str:
        """只列本轮文件。不传 files 时返回空串——禁止整库 `git diff --stat`。

        整库摘要会把工作区里无关的未提交改动（编排器、权限表、别的书）
        混进导入会话的「文件改动」卡，标题写 1 个文件、正文却列出几十个。
        """
        if not files:
            return ""
        return self._git("diff", "--stat", "--", *files)

    def changed_line_count(self, files: list[str]) -> int:
        """本次改动的行数（新增+删除）。未跟踪的新文件按其行数计。"""
        if not files:
            return 0
        total = 0
        numstat = self._git("diff", "--numstat", "--", *files)
        counted: set[str] = set()
        for line in numstat.splitlines():
            cols = line.split("\t")
            if len(cols) < 3:
                continue
            added, removed, path = cols[0], cols[1], cols[2]
            counted.add(path)
            for col in (added, removed):
                if col.isdigit():
                    total += int(col)
        for rel in files:
            if rel in counted:
                continue
            fp = self.workspace / rel
            if not fp.is_file():
                continue
            try:
                total += sum(1 for _ in fp.open("r", encoding="utf-8", errors="replace"))
            except OSError:
                continue
        return total

    def diff_patch(self, files: list[str], max_chars: int = 12000) -> str:
        """指定文件的补丁文本（供前端 diff 卡展示）。

        已跟踪文件走 git diff；未跟踪（新增）文件给出头部预览。
        """
        parts: list[str] = []
        tracked = self._git("diff", "--no-color", "--", *files) if files else ""
        if tracked.strip():
            parts.append(tracked)
        covered = {
            ln[len("diff --git a/") :].split(" b/", 1)[0]
            for ln in tracked.splitlines()
            if ln.startswith("diff --git a/")
        }
        for f in files:
            if f in covered:
                continue
            fp = self.workspace / f
            if not fp.is_file():
                parts.append(f"--- {f}（已删除）")
                continue
            try:
                head = fp.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError as e:
                head = f"<无法读取: {e}>"
            parts.append(f"+++ {f}（新增文件，头部预览）\n" + "\n".join(
                "+" + ln for ln in head.splitlines()[:40]
            ))
        out = "\n".join(parts)
        return out if len(out) <= max_chars else out[:max_chars] + f"\n...(截断，共{len(out)}字符)"

    def cleanup(self) -> None:
        if self._backup_dir and self._backup_dir.exists():
            shutil.rmtree(self._backup_dir, ignore_errors=True)
            self._backup_dir = None
