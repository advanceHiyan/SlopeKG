# 边坡唯一身份与资料核对接口

采用两层入库：**原件和识别结果先持久保存，身份核对后才关联边坡主档**。首次识别未知对象不创建主档。核对任务关闭、失败或暂时无人处理，不影响已保存的资料。

## 流程与设计依据

1. 识别完成或部分完成 → 保存原件指纹、副本、来源对象、原文候选、批次和版本。
2. 来源未关联实际边坡 → 进入“待核对资料”，按同一来源的明确父子关系归组。
3. 根据登记编号、名称及已确认来源别名、路线、桩号和左右侧提出候选，显示依据和冲突。匹配排序不是准确率或概率，也不会自动确认新来源。
4. 核对人选择已有主档，或确认是新边坡后创建永久 ID。重名候选要说明为何是另一处边坡；编号唯一约束不能通过“忽略”绕过。
5. 已确认的同源绑定可在后续识别版本中复用。来源名称变化时仍进入待核对区，不会悄悄生成另一处边坡。不同文件、不同批次需确认归属，之后聚合到同一永久 ID 下。
6. 错绑可更正或解除，原件、候选、历史决定均保留。整处主档合并不作为正常入库步骤，本版未提供主档合并功能。

参考的是主数据系统的职责分离，而非引入外部服务：Microsoft Dataverse 将 GUID 主键与业务唯一键分开，并在建档/导入时检查重复；AWS Entity Resolution 的匹配输出也保留匹配标识与匹配依据。本项目进一步把未核对来源留在资料区，避免识别错误直接污染正式身份。

- [Dataverse：业务唯一键](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/define-alternate-keys-entity)
- [Dataverse：重复检查与人工合并](https://learn.microsoft.com/en-us/power-apps/user/merge-duplicate-records)
- [AWS Entity Resolution：匹配标识与匹配依据](https://docs.aws.amazon.com/entityresolution/latest/userguide/find-match-id.html)

## 身份与时间边界

- `slopes.id` 是永久主键。既有 63 处边坡原 ID 保留；新建使用服务端生成的完整 UUID。名称、甲方编号、文件名、文件指纹和识别时间均不用于重算主键。
- `namespace + external_id` 是经人工登记的业务唯一键，使用数据库组合主键约束。所属范围可为单位、项目或编码体系；只有编号而没有范围不能登记。
- 同一主档更换外部编号后，旧编号仍指向原主档，不把旧编号悄悄分配给另一处边坡。系统生成的 `G209-AUTO-...` 不自动视为甲方正式编号。
- 路线、桩号、左右侧及名称是核对线索，不能单独保证身份。坐标系、里程调整、边坡分拆/合并等未建立权威依据时，不能自动推断。测量控制点不当边坡坐标。
- 新来源的自动匹配仅提出建议；模型识别出的 `slope_id` 或相同名称不直接授予正式关联权限。
- `document_date` 为资料原有日期，`recognized_at` 为识别运行时间，`created_at` 为保存时间。没有原日期时明确留空，不用文件修改时间或入库时间替代。事实自身的工况、范围和时间原样保留，不用新批次覆盖旧值。本轮不建设时序监测或评估表。
- 主档身份版本或来源身份线索发生变化后，原关联显示需重核。`identity_ready` 只表示该来源的身份决定仍适用，不代表工程参数已确认，也不是风险评估资格。

## 接口

沿用 `/api/slope-library` 的原件保存和查询接口，增加独立的身份接口；均使用现有同源写入保护。

| 方法与路径 | 输入 | 作用 |
| --- | --- | --- |
| GET `/api/slope-library/identity?slope_id=...` | 永久 ID | 查询身份字段、修订号、历史业务编号和维护记录 |
| GET `/api/slope-library/identity?namespace=...&external_id=...` | 完整业务唯一键 | 查找已登记身份；不存在返回 404，不自动新建 |
| GET `/api/slope-library/identity?binding_id=...&slope_id=...` | 来源绑定 ID，可选目标 ID | 获取来源线索、候选、冲突、关联组及修订号；无写入决定 |
| POST `/api/slope-library/identity` | `slope_id, revision, name, attributes, actor, reason, confirmed` | 人工登记或维护身份；永久 ID 不变 |
| POST `/api/slope-library`，`operation=bind` | 见下文 | 关联、新建或解除来源归属 |

`attributes` / 新建时的 `identity` 只接受：`namespace, external_id, route_code, side, start_station_m, end_station_m`。桩号以米表示；起终点需成对、有限且非负；左右侧只接受左侧、右侧或留空。不强迫填写原件中没有的信息。

确认来源归属的公共输入为 `binding_id, revision, actor, reason, confirmed=true`。通过 `include_related=true` 和完整的 `related_revisions` 一次核对本来源的相关对象，仍保留各自局部范围。

- **关联已有主档：**填写 `slope_id, target_revision`。服务端重新计算冲突；存在冲突时还须 `conflict_confirmed=true, conflict_reason`。
- **新建主档：**留空 `slope_id`，提供 `name, identity`。已有相似主档时须 `distinct_confirmed=true, distinct_reason`；与来源线索冲突时也须单独说明。创建主档和关联来源在同一事务中完成，失败整体回滚。
- **解除错误归属：**`release=true`，资料退回待核对区，不删除主档或原件。
- **过期请求或编号冲突：**HTTP 409，`error_type=identity_conflict`，要求刷新或核对；不会静默覆盖。格式问题返回 400，来源/身份不存在返回 404。

SQLite 使用写事务串行化核对与身份维护；来源修订号和目标身份修订号共同防止并发覆盖。重复入库沿用不可变快照指纹，不重复建档；重复提交同一旧修订号返回冲突。

决定记录包含核对人、依据、源/目标 ID、来源身份线索、目标修订号、匹配方法版本、冲突和明确处理理由。解除或更正后，旧关联不再参与当前别名推荐，但历史决定仍可查询。

## 既有资料与验证范围

新资料库直接显示既有边坡字段及旧档案详情入口，标明历史提取状态。此为只读接入，不把历史字段冒充已归档原件、当前识别或人工签认。旧资料批量迁移、模拟任务混入正式库均不自动执行。

本轮只改变入库后的身份管理和展示，不改识别提示词或工程判定规则，不调用模型，不恢复知联与视读开发。验证使用隔离数据库、真实本地测试 PDF、跨批次原值保持、冲突/重复/并发测试，以及正常入口浏览器核对。它们验证软件行为，不证明仅凭相似名称就能可靠认定工程对象。
