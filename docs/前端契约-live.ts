/**
 * 赛事直播模块字段类型：与后端 Resource/FormRequest 一一对应
 * 字段来源：backend/app/Http/Resources/Live/* 与 backend/app/Http/Requests/Admin/Live/*，禁止自编字段
 */

export type GroupType = 'university' | 'middle_school' | 'primary_school'
export type MatchStatus = 'not_started' | 'live' | 'finished'
export type EventType = 'goal' | 'yellow' | 'red' | 'substitution' | 'text'
export type TeamSide = 'home' | 'away'
export type RefereeRole = 'chief' | 'assistant' | 'fourth'
export type MatchFileType = 'sign_in' | 'record_confirm' | 'referee_report'
export type VerifyResult = 'pass' | 'fail' | 'pending'

/** 阵容球员（LineupRequest：name/no 必填，position/is_captain 可空） */
export interface LineupPlayer {
  name: string
  no: string
  position?: string
  is_captain?: boolean
}

/** GET /matches 列表项（MatchListResource） */
export interface MatchListItem {
  id: number
  group_type: GroupType
  group_type_label: string
  home_team_name: string
  away_team_name: string
  home_team_logo: string | null
  away_team_logo: string | null
  match_time: string | null
  venue: string | null
  status: MatchStatus
  status_label: string
  home_score: number
  away_score: number
}

export interface RefereeItem {
  role: RefereeRole
  role_label: string
  name: string
}

/** GET /matches/{id}（MatchDetailResource） */
export interface MatchDetail extends MatchListItem {
  lineup_home: LineupPlayer[] | null
  lineup_away: LineupPlayer[] | null
  live_link: string | null
  referees?: RefereeItem[]
}

/** 管理端详情（AdminMatchDetailResource：附 version 与审计字段） */
export interface AdminMatchDetail extends MatchDetail {
  version: number
  created_by: number
  updated_by: number
  created_at: string
  updated_at: string
}

/** 事件（MatchEventResource） */
export interface MatchEventItem {
  id: number
  type: EventType
  type_label: string
  minute: number
  team_side: TeamSide | null
  player_name: string | null
  player_no: number | null
  related_player_name: string | null
  content: string | null
  created_at: string
}

/** 事件增删改响应：事件 + 最新比分/版本号（局部更新用） */
export interface EventActionResult {
  event?: MatchEventItem
  home_score: number
  away_score: number
  version: number
}

/** 赛事文件（MatchFile 模型序列化） */
export interface MatchFileItem {
  id: number
  match_id: number
  type: MatchFileType
  file_name: string
  file_path: string
  mime: string
  size: number
  uploaded_by: number
  created_at: string
}

/** 人脸核验记录（FaceVerifyRecord 模型序列化） */
export interface FaceVerifyRecordItem {
  id: number
  match_id: number
  player_name: string
  player_no: number | null
  photo_path: string | null
  result: VerifyResult
  score: string | null
  channel: string
  request_id: string | null
  remark: string | null
  created_by: number
  created_at: string
}

/** 比赛新增/编辑载荷（MatchStoreRequest/MatchUpdateRequest） */
export interface MatchPayload {
  group_type: GroupType
  home_team_name: string
  away_team_name: string
  home_team_logo?: string | null
  away_team_logo?: string | null
  match_time: string
  venue?: string | null
}

/** 事件新增载荷（EventStoreRequest），client_token 前端生成 UUID 防重 */
export interface EventPayload {
  type: EventType
  minute: number
  team_side?: TeamSide | null
  player_name?: string | null
  player_no?: number | null
  related_player_name?: string | null
  content?: string | null
  client_token: string
}

export type EventUpdatePayload = Partial<Omit<EventPayload, 'client_token'>>
