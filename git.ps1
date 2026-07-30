<#
.SYNOPSIS
    Git 管理工具 —— 备份、还原、查看状态一条龙
.DESCRIPTION
    给 cloth-system 项目用的 Git 快捷管理器。日常用 .\git.ps1 backup 就能做一次快照。

    命令列表:
      status      查看工作区状态
      backup      快速备份（自动提交）
      save        同上（别名）
      list        查看历史版本
      log         同上（别名）
      restore     还原到指定版本
      diff        查看当前改动
      config      配置远程仓库（可选）
      gc          清理/压缩仓库
      help        显示本帮助
#>

param(
    [Parameter(Position=0)]
    [ValidateSet("status","backup","save","list","log","restore","diff","config","gc","help","undo","branch","squash")]
    [string]$Command = "help",

    [Parameter(Position=1)]
    [string]$Arg1,

    [switch]$AutoAdd,
    [switch]$Force
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
    Write-Host "❌ 当前目录不是 Git 仓库，请先运行 git init" -ForegroundColor Red
    exit 1
}

function Show-Status {
    Write-Host "`n📊 工作区状态" -ForegroundColor Cyan
    git -C $RepoRoot status -s
    $porcelain = git -C $RepoRoot status --porcelain
    if (-not $porcelain) {
        Write-Host "   ✅ 工作区干净，没有未提交的改动" -ForegroundColor Green
    }
    Write-Host ""
    git -C $RepoRoot log --oneline -5 2>$null
}

function Do-Backup {
    $msg = if ($Arg1) { $Arg1 } else { "备份 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" }

    # 默认自动 add 所有改动，除非加了 -NoAdd
    if ($AutoAdd -or ($Command -eq "backup" -and -not $env:GIT_NO_AUTO_ADD)) {
        git -C $RepoRoot add -A
        Write-Host "   📦 已暂存所有改动" -ForegroundColor Green
    }

    $status = git -C $RepoRoot status --porcelain
    if (-not $status) {
        Write-Host "   ℹ️  没有需要提交的改动" -ForegroundColor Yellow
        return
    }

    git -C $RepoRoot commit -m $msg
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   ✅ 备份成功: $msg" -ForegroundColor Green
        git -C $RepoRoot log --oneline -1
    }
}

function Show-History {
    $count = if ($Arg1) { [int]$Arg1 } else { 20 }
    git -C $RepoRoot log --oneline --graph --decorate -$count
}

function Do-Restore {
    if (-not $Arg1) {
        Write-Host "   ❌ 请指定要还原的 commit hash 或 索引(如 0=最新, 1=上一个)" -ForegroundColor Red
        Write-Host "   用法: .\git.ps1 restore <hash|索引>" -ForegroundColor Yellow
        return
    }

    # 支持索引: 0=HEAD, 1=HEAD~1, 2=HEAD~2 ...
    if ($Arg1 -match '^\d+$') {
        $idx = [int]$Arg1
        if ($idx -eq 0) {
            $hash = "HEAD"
        } else {
            $hash = "HEAD~$idx"
        }
    } else {
        $hash = $Arg1
    }

    # 确认
    $targetDesc = git -C $RepoRoot log --oneline -1 $hash 2>$null
    if (-not $targetDesc) {
        Write-Host "   ❌ 找不到这个版本: $hash" -ForegroundColor Red
        return
    }

    if (-not $Force) {
        Write-Host "⚠️  即将还原到: $targetDesc" -ForegroundColor Yellow
        Write-Host "   当前未提交的改动会丢失！建议先运行 .\git.ps1 backup" -ForegroundColor Yellow
        Write-Host "   确认请按 Y，取消按其他键：" -ForegroundColor Magenta -NoNewline
        $key = [Console]::ReadKey($true)
        Write-Host ""
        if ($key.Key -ne "Y") {
            Write-Host "   ❌ 已取消" -ForegroundColor Red
            return
        }
    }

    # 先保存当前未提交的改动（如果有）
    $dirty = git -C $RepoRoot status --porcelain
    if ($dirty) {
        Write-Host "   💾 正在暂存当前未提交的改动..." -ForegroundColor Cyan
        git -C $RepoRoot stash push -m "自动 stash: 还原前 ($(Get-Date -Format 'HH:mm:ss'))"
    }

    git -C $RepoRoot reset --hard $hash
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   ✅ 已还原到: $targetDesc" -ForegroundColor Green
    }
}

function Show-Diff {
    $path = if ($Arg1) { $Arg1 } else { "." }
    git -C $RepoRoot diff -- $path
    $staged = git -C $RepoRoot diff --cached -- $path
    if ($staged) {
        Write-Host "`n📎 已暂存的改动:" -ForegroundColor Cyan
        git -C $RepoRoot diff --cached -- $path
    }
}

function Set-Remote {
    if (-not $Arg1) {
        $remotes = git -C $RepoRoot remote -v
        if ($remotes) {
            Write-Host "🌐 当前远程仓库:" -ForegroundColor Cyan
            $remotes
        } else {
            Write-Host "   ℹ️  尚未配置远程仓库" -ForegroundColor Yellow
            Write-Host "   用法: .\git.ps1 config <远程仓库URL>" -ForegroundColor Yellow
        }
        return
    }

    $remoteUrl = $Arg1
    $existing = git -C $RepoRoot remote get-url origin 2>$null
    if ($existing) {
        git -C $RepoRoot remote set-url origin $remoteUrl
    } else {
        git -C $RepoRoot remote add origin $remoteUrl
    }
    Write-Host "   ✅ 远程仓库已设置为: $remoteUrl" -ForegroundColor Green

    # 推送到远程
    Write-Host "   🚀 正在推送..." -ForegroundColor Cyan
    git -C $RepoRoot push -u origin --all
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   ✅ 推送成功" -ForegroundColor Green
    } else {
        Write-Host "   ⚠️  推送失败，检查 URL 或网络" -ForegroundColor Red
    }
}

function Do-GC {
    Write-Host "   🧹 正在清理压缩 Git 仓库..." -ForegroundColor Cyan
    git -C $RepoRoot gc --aggressive --prune=now
    Write-Host "   ✅ 仓库已优化" -ForegroundColor Green
}

function Do-Undo {
    Write-Host "⚠️  撤销上一次提交 (保留改动在工作区)" -ForegroundColor Yellow
    Write-Host "   确认请按 Y： " -ForegroundColor Magenta -NoNewline
    $key = [Console]::ReadKey($true)
    Write-Host ""
    if ($key.Key -ne "Y") {
        Write-Host "   ❌ 已取消" -ForegroundColor Red
        return
    }
    git -C $RepoRoot reset --soft HEAD~1
    Write-Host "   ✅ 已撤销上一次提交，改动保留在工作区" -ForegroundColor Green
}

function Show-Branches {
    Write-Host "🌿 分支列表" -ForegroundColor Cyan
    git -C $RepoRoot branch -a
    Write-Host ""
    Write-Host "当前分支: " -NoNewline
    git -C $RepoRoot branch --show-current
}

function Do-Squash {
    if (-not $Arg1) {
        Write-Host "   ❌ 请指定要合并的 commit 数量" -ForegroundColor Red
        Write-Host "   用法: .\git.ps1 squash <数量>" -ForegroundColor Yellow
        Write-Host "   示例: .\git.ps1 squash 3   # 合并最近3个提交" -ForegroundColor Yellow
        return
    }

    $count = [int]$Arg1
    if ($count -lt 2) {
        Write-Host "   ❌ 至少要合并 2 个提交" -ForegroundColor Red
        return
    }

    Write-Host "⚠️  即将合并最近 $count 个提交" -ForegroundColor Yellow
    Write-Host "   这会用 rebase 交互模式，请准备好编辑器" -ForegroundColor Yellow
    Write-Host "   确认请按 Y： " -ForegroundColor Magenta -NoNewline
    $key = [Console]::ReadKey($true)
    Write-Host ""
    if ($key.Key -ne "Y") {
        Write-Host "   ❌ 已取消" -ForegroundColor Red
        return
    }

    git -C $RepoRoot rebase -i HEAD~$count
}

function Show-Help {
    Write-Host @"
╔══════════════════════════════════════════════╗
║    🧵 cloth-system Git 管理工具              ║
╚══════════════════════════════════════════════╝

用法:  .\git.ps1 <命令> [参数]

📋 常用命令（按照使用频率排列）:
────────────────────────────────────────────
  status              查看当前改动状态
  backup [说明]       快速备份（自动 add + commit）
  list [数量]         查看历史版本（默认 20 条）
  restore <hash|索引> 还原到指定版本

🛠️ 其他命令:
────────────────────────────────────────────
  diff [文件路径]     查看具体改了什么
  undo                撤销上一次提交（改动保留）
  branch              查看分支
  squash <数量>       合并最近 N 次提交
  config [远程URL]    查看/配置远程仓库
  gc                  清理压缩仓库（省空间）
  help                显示本帮助

💡 日常快速备份:
    .\git.ps1 backup              # 一键备份（自动提交）
    .\git.ps1 backup "改了 xxx"    # 带说明的备份

💡 还原到某版本：
    .\git.ps1 restore 0           # 还原到最新版本
    .\git.ps1 restore 1           # 还原到上一个版本
    .\git.ps1 restore abc1234     # 还原到指定 hash
"@ -ForegroundColor Cyan
}

# ------ Main ------
switch ($Command) {
    "status"  { Show-Status }
    "backup"  { Do-Backup }
    "save"    { Do-Backup }
    "list"    { Show-History }
    "log"     { Show-History }
    "restore" { Do-Restore }
    "diff"    { Show-Diff }
    "config"  { Set-Remote }
    "gc"      { Do-GC }
    "undo"    { Do-Undo }
    "branch"  { Show-Branches }
    "squash"  { Do-Squash }
    default   { Show-Help }
}
