# -*- coding: utf-8 -*-
"""Renders the pitch teleprompter with hover-expandable hint terms."""
import html
import re

# ---- hint dictionary: term -> (title, body html) ----
HINTS = {
    "hilosophy": (
        "为什么说设计哲学扎实",
        "・<b>只记账、不涂改</b>：数据进来只会往上添，任何改动都留底——像银行流水，"
        "错了也能查到当初是谁、什么时候、记的什么<br>"
        "・<b>每条数据都有出处</b>：任何一条维修记录都能查回「来自哪个 Excel、哪张表、第几行」<br>"
        "・<b>拿不准的不自作主张</b>：两份数据打架时，系统不擅自选边，摆出来等人拍板，"
        "拍板记录永久保存<br>"
        "・<b>报表随时可以重算</b>：所有网页和报表都是从账本算出来的，"
        "全删了也能一键重新生成，分毫不差<br>"
        "・<b>不知道的就说不知道</b>：没有记录的时间段明确标「无记录」，不假装数据完整<br>"
        "<b>一句话总结：像一位记账极其严谨的档案员——只添不改、事事留底、"
        "疑问上报、账目随时可复核。</b>"),
    "dualvalue": (
        "为什么价值是双重的",
        "・<b>对客户</b>：车队一直缺一个靠谱的地方查维修记录——这套系统把他们粗糙的 Excel "
        "管起来、查得快、不出错，正是他们要的<br>"
        "・<b>对我们自己</b>：它是诊断 AI 的<b>第三个事实来源</b>。现在诊断靠两样——"
        "车的实时数据 + 维修手册；在现有架构上加一个 <b>History Agent</b>，"
        "诊断时就能把这台车<b>真实的维修史</b>、甚至<b>相似车辆的参考案例</b>也纳入进来。"
        "「这车之前修过什么」往往比手册更接近答案"),
    "xingtai": (
        "「形态」指什么",
        "这套系统作为一个服务怎么活着：<b>跑在谁的机器上</b>、<b>数据从哪进从哪出</b>、"
        "<b>谁能访问</b>、<b>AI 怎么接入</b>、<b>将来怎么扩</b>。"
        "它是后面所有集成工作的地基——形态不定，上传、页面、权限、AI 接入都无从谈起"),
    "testvalue": (
        "测试系统有什么用",
        "・<b>自动拦错</b>：以后任何人改代码、加功能，跑一遍测试就知道有没有把东西改坏——"
        "这 10 个 bug 一类的错误会被当场拦下，不用再靠人工一个个翻<br>"
        "・<b>让 AI 替我们找 bug</b>：测试就是一套「对错标准」。有了它，"
        "就能放 AI 在系统里自动试各种输入、自动验证——这次的 10 个 issue "
        "大半就是 AI 扫描+验证出来的，测试体系让这件事<b>常态化</b>"),
    "weakedge": (
        "边缘纪律较弱的 10 个实例",
        "#1 文件该放哪没定规矩，来新车队会乱<br>"
        "#2 纸质单据进不来，只能人工抄（低优先）<br>"
        "#3 导入全失败也显示「成功」，没人知道出事<br>"
        "#4 中文编码不对会存成乱码，还删不掉<br>"
        "#5 传错文件类型会被直接无视，毫无提示<br>"
        "#6 部件对照表有 10 条从来没生效过<br>"
        "#7 一个空文件能让整次导入全军覆没<br>"
        "#8 人工审核拼错一个词，系统执行相反操作<br>"
        "#9 表格缺项/错位/日期非法都照收不误<br>"
        "#10 文件名里的日期会被认成车牌，生出幽灵车<br>"
        "<i>7 个当场可复现，全部已提交 GitHub</i>"),
    "folder3": (
        "为什么叫「会算数的文件夹」",
        "・<b>克隆即部署</b>：程序+数据+页面全在一个文件夹里，下载下来就能用<br>"
        "・<b>双击即使用</b>：装环境、更新数据、开看板各是一个双击图标；"
        "看病历连程序都不用开，直接双击网页文件<br>"
        "・<b>推拉即同步</b>：多人同步数据靠程序员的代码同步工具（Git）——"
        "这恰是它对普通用户断掉的一环"),
    "locality": (
        "「数据在本地」是个误会",
        "听起来数据一直待在车间本地，其实<b>每次同步都传上了 GitHub</b>（境外的云服务器），"
        "被邀请的人随时能整库下载。而且权限只有「邀请名单」一档——"
        "能看就能看全部，含员工姓名电话。<br>"
        "所以「留在本地」不是隐私保护，只是当年没有服务器的将就。<br>"
        "<b>搬到我们服务器 + 登录授权，隐私反而是升级</b>：第一次能控制"
        "「谁、能看哪几台车」"),
    "whynotrebuild": (
        "为什么不彻底改造它",
        "它的可靠性恰恰建立在「简单」上：每天完整跑一遍、随时可重算、"
        "疑问交人裁决——这些优点全长在批处理这个根上。<br>"
        "彻底改成在线服务 = 动根基：工作量大、风险高，而 20 台车的数据量"
        "完全用不着。<b>先包装用起来，规模真上来了再谈改造</b>——到时也有真实使用经验打底"),
    "thingateway": (
        "薄网关是什么·为什么「薄」",
        "在服务器上加的一层小外壳，只干三件事：<br>"
        "・<b>收文件</b>：用户在网页上传维修记录 → 自动进系统<br>"
        "・<b>给页面</b>：登录后按权限看各自车辆的病历页<br>"
        "・<b>管权限</b>：谁能传、谁能看哪些车<br>"
        "说它「薄」，是因为对方的程序<b>一行代码都不用改</b>——外壳全是我们加的"),
    "sitelike": (
        "用户看到的是什么",
        "他的产出本来就是一组互相链接的网页：总览页列出全部车辆，点车牌进病历，"
        "点部件看详情。放上服务器 = 变成一个正常网站——有网址、有登录，"
        "浏览体验和现在<b>一模一样</b>，只是再也不用碰文件夹。<br>"
        "外加一个新能力：上传完当场告诉你「成功 N 条 / 有 N 条没归上」"),
    "nochange": (
        "为什么一行代码不用改",
        "上传、登录、权限全是我们在<b>外面</b>加的壳；他的程序在服务器上照原样定时跑。<br>"
        "分工天然清晰：他继续管核心程序和修 bug，我们管壳和服务器——"
        "互不阻塞，也不用他学任何新东西"),
    "gitrole": (
        "GitHub 的新角色",
        "以前 GitHub 身兼三职：<b>装机盘</b>（克隆=安装）、<b>同步网</b>（推拉=同步）、"
        "<b>备份</b>。搬家后前两职退休，只留备份和历史存档——"
        "服务器定期把数据存一版上去，哪天服务器出事也能整套恢复"),
    "ssot": (
        "「正本」在哪",
        "现状：GitHub 上一份 + 每个人电脑里各一份，谁的最新没人说得清。"
        "搬上服务器后：<b>服务器上那份是正本</b>——所有人看的、AI 读的都是同一份；"
        "GitHub 退居二线只做备份和历史存档"),
    "noaddr": (
        "为什么 AI 读不了",
        "AI 要接入，前提是数据有个<b>固定的家</b>——一个随时在线、路径不变的地方。"
        "「某人电脑里的文件夹」今天开机明天关机，每个人路径还不一样，AI 没法依赖。"
        "搬到服务器后，数据第一次有了固定地址，History Agent 才有地方可读"),
    "copies": (
        "为什么越用越散",
        "现在每多一个使用者 = 多复制一整份文件夹。副本之间<b>不会自己同步</b>——"
        "有人更新了、有人没更新，时间一长满天都是不同版本，谁也说不清哪份可信。"
        "车队和用户越多，这个问题只会指数式变糟"),
    "prefixauth": (
        "按文件夹管数据，也按文件夹给权限",
        "数据按「车队/批次」分文件夹存（issue #1 的提议），权限也按文件夹划——"
        "A 车队的账号只能进 A 的文件夹。"
        "<b>数据怎么放和权限怎么给用同一套规矩</b>，加新车队就是新建一个文件夹的事"),
    "goldentests": (
        "现有测试是什么",
        "5 个测试干的事：拿固定的老数据跑一遍，检查结果和上次一样。<br>"
        "能防：改代码把现有结果改坏。<br>"
        "不能防：<b>新数据进来会发生什么</b>——而我们找到的 10 个 bug 全是新数据引发的"),
    "fivelayers": (
        "五层测试清单",
        "①<b>零件级</b>：把解析日期、认车牌这类小函数单独测（bug 最集中的地方）<br>"
        "②<b>坏输入</b>：故意喂乱码、空文件、缺项的表格，看它接不接<br>"
        "③<b>老数据快照</b>：已有，保留<br>"
        "④<b>自动查账</b>：每次跑完自检账目（见另一条）<br>"
        "⑤<b>全流程</b>：模拟真实用户从上传到看页面走几遍<br>"
        "节奏：① 1-2 天 → ④ 1 天 → 全自动化半天"),
    "badinput": (
        "坏输入测试从哪来",
        "不用凭空设计——10 个 issue 每条都写了「修好的标准」，那就是现成的测试题：<br>"
        "修乱码问题 → 附带「乱码文件要被拒收」的测试；"
        "修空文件问题 → 附带「一个空文件不能连累全部」……<br>"
        "<b>修完 10 个 bug，这层测试自动就有了</b>"),
    "invariants": (
        "「自动查账」是什么",
        "每次跑完数据，自动核几条账：<br>"
        "・老记录一条都不能少<br>"
        "・分到各车的 + 没归上的 = 总数，不多不少<br>"
        "・每条记录都查得到出处<br>"
        "・日期区间不能头尾颠倒<br>"
        "实现就是几句查询语句。<b>每晚查账全过 = 我们敢在页面上承诺「数据准确」</b>"),
    "p0policy": (
        "为什么这条政策是杠杆",
        "一句话政策：<b>修哪个 bug，就顺手写一个「证明它修好了」的测试</b>。"
        "写进交接说明就行，不用单独立项——10 个 bug 修完，测试体系的骨架自动成形"),
    "excelsorigin": (
        "为什么这个问题最关键",
        "它揭示<b>数据的真实源头</b>：是车间师傅直接在 Excel 里录？还是别的系统导出的？"
        "中间有没有纸质单据环节？谁在日常维护这些表？<br>"
        "答案直接决定：上传口设计给谁用、要不要做纸质转录（issue #2 留的口）、"
        "以及数据质量问题该在源头哪一环治理"),
    "expandq": (
        "为什么现在就要问",
        "单车队和多车队在<b>文件夹和权限的设计上是岔路口</b>：现在知道方向，"
        "一开始就按车队分好文件夹（零成本）；不知道，将来再改就要搬数据。<br>"
        "问的只是<b>意图</b>，不需要对方承诺"),
    "opsq": (
        "想收集的具体信息",
        "・谁负责上传：车间自己传，还是先由我们代传？<br>"
        "・大概多久传一批新记录？<br>"
        "・哪些人需要登录看页面？有没有人要看全部车？<br>"
        "答案决定：开几个账号、权限分几档、上传口做多简单"),
    "sysjob": (
        "这套系统是干什么的",
        "把车队维修记录（人工填的 Excel）自动整理成<b>每台车一份的电子病历</b>：<br>"
        "这台车修过什么、什么时候修的、换过哪些零件、每个零件用了多久。<br>"
        "目的：给车队一个能查能看的档案库，也为分析零件寿命准备干净数据。<br>"
        "它刻意<b>不做</b>：健康评分、寿命预测——作者认为数据还不够完整，不配下结论"),
    "sysinput": (
        "三个数据入口",
        "・<b>存量</b>：车队交来的 21 个 Excel（20 台车的历史维修记录）——已导入<br>"
        "・<b>增量</b>：新文件丢进一个「收件目录」，跑一次程序就进系统<br>"
        "・<b>人工补录</b>：不在任何文件里的事实（如口头确认的维修），按固定表格填一行<br>"
        "另外直接修改原 Excel 也可以——系统靠文件指纹发现变化"),
    "sysledger": (
        "记账这一步做什么",
        "・每个文件算一个「指纹」：内容变过就记新版本，没变就跳过<br>"
        "・每行记录生成唯一编号：同一条记录出现在几个文件里只记一次，"
        "但记下它都在哪出现过<br>"
        "・账本<b>只添不改</b>：任何修改都保留旧版，全程可追溯<br>"
        "・每条数据都记得自己的出处：哪个文件、哪张表、第几行"),
    "sysreview": (
        "审核这一步做什么",
        "同一条记录这次导入内容变了（比如日期不一样）→ 系统<b>不自动覆盖</b>：<br>"
        "・旧版继续生效，新版挂起，进「待审清单」<br>"
        "・人在决定表里填：采纳新版 / 保留旧版 / 整条作废<br>"
        "・裁决执行后<b>永久存档</b>，同一个问题不会问第二遍<br>"
        "现状：有 9 条真实冲突正等着裁决（同一工单在不同表里日期对不上）"),
    "sysbuild": (
        "建档这一步做什么",
        "把账本里的记录按车牌分家，每台车算出：<br>"
        "・车辆档案 + 部件清单（按车型自动裁剪：电车没有机油滤芯）<br>"
        "・维修事件时间线（可搜索、可按系统筛）<br>"
        "・每个部件的「第几代、装了多久、怎么退役的」<br>"
        "・数据缺口明确标注「无记录」，发现的数据毛病单独列清单<br>"
        "归属不了任何车的记录进「隔离区」存档（现有 404 条待分诊）"),
    "sysoutput": (
        "每台车的三件套",
        "・<b>病历网页</b>：总览页列全部车 → 点车牌进单车页 → 点部件看详情，"
        "纯点击浏览<br>"
        "・<b>数据库文件</b>：结构化数据，给 AI 和分析工具读<br>"
        "・<b>表格导出</b>：10 个 Excel 能开的文件（事件、零件寿命等）<br>"
        "三样全是「打印件」：删光了也能从账本一键重算，分毫不差"),
    "sysnums": (
        "关键数字备忘",
        "20 台车 · 21 个源文件 · 2165 条已接受证据 · 1197 个维修事件 · "
        "544 张工单<br>404 条隔离区记录待分诊（约占原始记录两成）· "
        "9 条冲突待人工裁决 · 现有测试 5 个<br>"
        "车队构成很杂：16 种车型（丰田海狮/日产 Leaf/雅马哈三轮…），"
        "同型号最多 2 台"),
    "northstar": (
        "北极星 v2（被问「长期怎么办」时用）",
        "长期目标架构：并入 STF 平台，作为它的一个数据域——<br>"
        "・所有记录进平台数据库（多车队 = 加一列，不是加目录）<br>"
        "・数据进门时逐行体检，坏行当场退回并说明原因<br>"
        "・复核在网页上点按钮，谁裁决的自动留名<br>"
        "・AI 只能读「脱敏视图」——不是约定不看隐私，是<b>物理上读不到</b><br>"
        "但<b>现在不做</b>：20 台车不值得重写。打法是包装版先上线，"
        "哪块撞墙迁哪块——他的账本可整体重放，<b>迁移 = 重放导入</b>，天然低风险。"
        "保留他的三个核心思想不变：证据不可变、人工裁决、随时可重算"),
    "notdo": (
        "哪些不做、为什么",
        "压力测试（模拟海量用户同时访问）、故障演练、页面自动点击测试、"
        "覆盖率指标考核——这些是大流量在线服务的装备。<br>"
        "这套系统是每天跑一次的小批处理、20 台车的数据量，"
        "上这些纯属浪费，把钱花在「喂坏数据」和「自动查账」上才对症"),
}

MD = r"""
# 服务形态提案 · 提词器

<p class="usage">虚线下划线的词可悬停展开 · 点击可钉住 · Esc 收起</p>

## ① 开场（30 秒）

- 我审核了这套系统，定性一句话：{{hilosophy|设计哲学非常扎实}}；但也暴露了{{weakedge|边缘纪律较弱}}的问题
- 这套系统对我们的{{dualvalue|价值是双重的}}
- 我做了什么：
  - 针对细节 bug 提了 **10 个 issue**（7 个可复现、4 个带机制图）——给你作为修复参考
  - 思考了这个系统的{{xingtai|形态}}
- 今天谈**两件事**：
  - 以这 10 个 issue 为例，如何建立{{testvalue|一套完善的测试系统}}
  - 把系统的**高级架构 / 形态**定下来

## ② 现状画像（1 分钟）

- 它现在不是一个"服务"，是{{folder3|一个会算数的文件夹}}——下载下来就能用，看病历双击网页即可
- 但要拿它当正式服务用，三个坎绕不过去：
  - 车队看数据：{{ssot|大家手里的不是同一份}}
  - AI 读数据：{{noaddr|AI 找不到固定的地方去读}}
  - 往大了扩：{{copies|越用副本越多、越散}}

## ③ 提案（2 分钟 · 核心）

- 一句话：{{whynotrebuild|不改造它，包装它}}——把文件夹搬上我们的服务器，门口加一道{{thingateway|薄网关}}
- 搬完之后，各方看到的变化：
  - 车队用户：登录后是{{sitelike|一个正常的病历网站}}——上传和查看都在网页里完成
  - 开发者：{{nochange|程序一行不用改}}，照常开发照常修 bug
  - AI：数据终于有了固定的家，History Agent 直接读
- 服务器上那份从此是**正本**；{{gitrole|GitHub 退居备份}}
- 将来加新车队 = {{prefixauth|新建一个文件夹的事}}
- 若被问"数据离开本地了"：{{locality|数据其实从来就没在本地}}

## ④ 第二件事：测试怎么建（2 分钟）

- 现状：{{goldentests|现有测试只检查「老数据还算得对不对」}}——我们发的 10 个 bug，它**一个都拦不住**
- 补法：{{fivelayers|一套分层的测试}}，重点投两层：
  - {{badinput|喂坏数据}}：故意塞乱码、空文件、缺项的表格，确认系统会**拒收或报警**
  - {{invariants|自动查账}}：每次跑完数据自动核几条账——敢承诺"数据准确"的底气
- 落地方式（零成本）：{{p0policy|修哪个 bug，就顺手写一个证明它修好的测试}}
- 建成之后的{{testvalue|长期价值}}：自动拦住未来的错 + 让 AI 替我们持续找 bug
- 明确不做：{{notdo|大公司那套重型测试}}——这个规模用不上

## ⑤ 会上要收集的输入（别忘了问）

- [ ] 那 21 个 Excel 当初是{{excelsorigin|怎么产生、怎么交到我们手上的}}？
- [ ] 后续要不要{{expandq|扩到更多车、更多车队}}？
- [ ] 部署到车间之后，{{opsq|日常怎么操作}}？

## 附录 · 系统备忘（不讲，忘了细节看这里）

- 它是干什么的：{{sysjob|把车队维修记录变成每台车的电子病历}}
- 数据从哪进：{{sysinput|三个入口}}
- 第一步 · 记账：{{sysledger|所有记录进一本只添不改的账}}
- 第二步 · 审核：{{sysreview|数据打架时人来裁决}}
- 第三步 · 建档：{{sysbuild|按车牌给每台车建病历}}
- 最终产出：{{sysoutput|每台车三件套}}
- 关键数字：{{sysnums|20 台车 · 2165 条证据 · 404 条待分诊}}
- 长期怎么办：{{northstar|北极星 v2 —— 撞到墙再迁，迁移天然低风险}}
"""


def md_to_html(md: str) -> str:
    lines_out = []
    in_ul = False
    for raw in md.strip().split("\n"):
        line = raw.rstrip()
        if not line:
            if in_ul:
                lines_out.append("</ul>")
                in_ul = False
            continue
        indent2 = line.startswith("  - ")
        is_li = line.lstrip().startswith("- ") or line.lstrip().startswith("- [ ]")
        content = line.lstrip()
        if content.startswith("# "):
            if in_ul: lines_out.append("</ul>"); in_ul = False
            lines_out.append(f"<h1>{content[2:]}</h1>")
            continue
        if content.startswith("## "):
            if in_ul: lines_out.append("</ul>"); in_ul = False
            lines_out.append(f"<h2>{content[3:]}</h2>")
            continue
        if content.startswith("<p"):
            lines_out.append(content)
            continue
        if is_li:
            if not in_ul:
                lines_out.append("<ul>")
                in_ul = True
            body = content[2:]
            if body.startswith("[ ] "):
                body = "☐ " + body[4:]
            cls = ' class="sub"' if indent2 else ""
            lines_out.append(f"<li{cls}>{body}</li>")
            continue
        if in_ul:
            lines_out.append("</ul>"); in_ul = False
        lines_out.append(f"<p>{content}</p>")
    if in_ul:
        lines_out.append("</ul>")
    out = "\n".join(lines_out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    return out


def inject_hints(html_text: str) -> str:
    def repl(m):
        key, label = m.group(1), m.group(2)
        title, body = HINTS[key]
        return (f'<span class="hint" data-title="{html.escape(title)}" '
                f'data-tip="{html.escape(body)}">{label}</span>')
    return re.sub(r"\{\{(\w+)\|(.+?)\}\}", repl, html_text)


body = inject_hints(md_to_html(MD))

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>服务形态提案提词器</title>
<style>
  :root { --bg:#1e2126; --panel:#2a2e36; --border:#3d4350; --text:#dcdad4;
          --dim:#9a9890; --accent:#8ab4d8; --hint:#e8c47a; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
         font-size:16px; line-height:1.9; }
  main { max-width:820px; margin:0 auto; padding:32px 28px 120px; }
  h1 { font-size:1.5em; border-bottom:2px solid var(--border); padding-bottom:10px; }
  h2 { font-size:1.15em; margin-top:2em; color:var(--accent); }
  ul { padding-left:1.3em; margin:.4em 0; }
  li { margin:4px 0; }
  li.sub { list-style:circle; margin-left:1.2em; }
  .usage { color:var(--dim); font-size:.85em; margin-top:-6px; }
  .hint { border-bottom:1.5px dashed var(--hint); cursor:help; color:var(--hint); }
  .hint.pinned { background:rgba(232,196,122,.14); border-radius:3px; }
  #tip { position:absolute; z-index:99; max-width:540px; min-width:300px;
         background:var(--panel); border:1px solid var(--hint); border-radius:10px;
         padding:12px 16px; font-size:.9em; line-height:1.75;
         box-shadow:0 8px 28px rgba(0,0,0,.45); display:none; }
  #tip .t { color:var(--hint); font-weight:600; margin-bottom:6px; }
  #tip.pinned { border-width:2px; }
</style>
</head>
<body>
<main>__BODY__</main>
<div id="tip"><div class="t"></div><div class="b"></div></div>
<script>
(function(){
  var tip = document.getElementById('tip');
  var tt = tip.querySelector('.t'), tb = tip.querySelector('.b');
  var current = null, pinned = false, hideTimer = null;
  function show(el){
    if (pinned) return;
    current = el;
    tt.textContent = el.dataset.title;
    tb.innerHTML = el.dataset.tip;
    tip.style.display = 'block';
    var r = el.getBoundingClientRect();
    var top = r.bottom + window.scrollY + 8;
    var left = Math.min(r.left + window.scrollX, window.scrollX + document.documentElement.clientWidth - tip.offsetWidth - 16);
    if (top + tip.offsetHeight > window.scrollY + window.innerHeight - 10)
      top = r.top + window.scrollY - tip.offsetHeight - 8;
    tip.style.top = top + 'px';
    tip.style.left = Math.max(left, 8) + 'px';
  }
  function scheduleHide(){
    if (pinned) return;
    hideTimer = setTimeout(function(){ tip.style.display='none'; current=null; }, 350);
  }
  function cancelHide(){ if (hideTimer) { clearTimeout(hideTimer); hideTimer=null; } }
  document.querySelectorAll('.hint').forEach(function(el){
    el.addEventListener('mouseenter', function(){ cancelHide(); show(el); });
    el.addEventListener('mouseleave', scheduleHide);
    el.addEventListener('click', function(e){
      e.stopPropagation();
      if (pinned && current === el) { unpin(); return; }
      pinned = false; show(el); pinned = true;
      tip.classList.add('pinned');
      document.querySelectorAll('.hint.pinned').forEach(h=>h.classList.remove('pinned'));
      el.classList.add('pinned');
    });
  });
  tip.addEventListener('mouseenter', cancelHide);
  tip.addEventListener('mouseleave', scheduleHide);
  function unpin(){
    pinned = false; tip.classList.remove('pinned');
    document.querySelectorAll('.hint.pinned').forEach(h=>h.classList.remove('pinned'));
    tip.style.display='none'; current=null;
  }
  document.addEventListener('click', function(){ if (pinned) unpin(); });
  document.addEventListener('keydown', function(e){ if (e.key==='Escape') unpin(); });
})();
</script>
</body>
</html>"""

with open("pitch_notes.html", "w", encoding="utf-8") as f:
    f.write(PAGE.replace("__BODY__", body))
print("rendered pitch_notes.html with", len(HINTS), "hints")
