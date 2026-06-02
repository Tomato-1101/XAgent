import type { RecommendedSlot } from "../types";

/**
 * 予約時刻ピッカー(JST壁時計)。値は "YYYY-MM-DDTHH:MM"。
 *
 * datetime-local の代替。クイック(1時間後/今夜21時など)・おすすめ時間・
 * 日付/時刻ドロップダウンの3段で、年月日時分を直接いじらず選べるようにする。
 */

const JA_WD = ["日", "月", "火", "水", "木", "金", "土"];

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** Date を JST 壁時計の年月日時分に分解する。 */
function jstParts(date: Date) {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const p = Object.fromEntries(fmt.formatToParts(date).map((x) => [x.type, x.value]));
  return { y: +p.year, mo: +p.month, d: +p.day, h: +p.hour, mi: +p.minute };
}

function todayYmd(): string {
  const n = jstParts(new Date());
  return `${n.y}-${pad(n.mo)}-${pad(n.d)}`;
}

function addDaysYmd(ymd: string, days: number): string {
  const [y, mo, d] = ymd.split("-").map(Number);
  const dt = new Date(Date.UTC(y, mo - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}`;
}

/** "YYYY-MM-DD" → "M/D(曜)" */
function dateLabel(ymd: string): string {
  if (!ymd) return "";
  const [y, mo, d] = ymd.split("-").map(Number);
  const wd = JA_WD[new Date(Date.UTC(y, mo - 1, d)).getUTCDay()];
  return `${mo}/${d}(${wd})`;
}

function splitValue(v: string): { date: string; time: string } {
  const [date, time] = (v || "").split("T");
  return { date: date || "", time: (time || "").slice(0, 5) };
}

/** 現在からの相対分で JST 壁時計値を作る(クイック「N時間後」用)。 */
function jstValueFromNow(addMinutes: number): string {
  const n = jstParts(new Date(Date.now() + addMinutes * 60000));
  return `${n.y}-${pad(n.mo)}-${pad(n.d)}T${pad(n.h)}:${pad(n.mi)}`;
}

/** 次に来る JST 指定時刻(hour:minute)の値。今日を過ぎていれば明日。 */
function nextJstHm(hour: number, minute = 0): string {
  const n = jstParts(new Date());
  let ymd = `${n.y}-${pad(n.mo)}-${pad(n.d)}`;
  if (hour < n.h || (hour === n.h && minute <= n.mi)) ymd = addDaysYmd(ymd, 1);
  return `${ymd}T${pad(hour)}:${pad(minute)}`;
}

const QUICKS: { label: string; value: () => string }[] = [
  { label: "1時間後", value: () => jstValueFromNow(60) },
  { label: "3時間後", value: () => jstValueFromNow(180) },
  { label: "今夜 21:00", value: () => nextJstHm(21) },
  { label: "明日 8:00", value: () => `${addDaysYmd(todayYmd(), 1)}T08:00` },
];

const SELECT_CLASS =
  "h-9 rounded-md border border-zinc-700 bg-zinc-950 px-2 text-sm text-zinc-100 " +
  "focus:outline-none focus:ring-2 focus:ring-sky-500/50";

const CHIP_CLASS = "rounded-full border border-zinc-700 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-800";

export function SchedulePicker({
  value,
  onChange,
  recommended = [],
}: {
  value: string;
  onChange: (v: string) => void;
  recommended?: RecommendedSlot[];
}) {
  const cur = splitValue(value);
  const today = todayYmd();

  // 日付候補: 今日〜2週間先。現在値が範囲外ならそれも含める。
  const dates = Array.from({ length: 15 }, (_, i) => addDaysYmd(today, i));
  if (cur.date && !dates.includes(cur.date)) dates.unshift(cur.date);

  // 時刻候補: 15分刻み。現在値が刻みに乗らなければ含める。
  const times: string[] = [];
  for (let h = 0; h < 24; h++) for (let m = 0; m < 60; m += 15) times.push(`${pad(h)}:${pad(m)}`);
  if (cur.time && !times.includes(cur.time)) {
    times.push(cur.time);
    times.sort();
  }

  function setDate(date: string) {
    onChange(`${date}T${cur.time || "21:00"}`);
  }
  function setTime(time: string) {
    onChange(`${cur.date || today}T${time}`);
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <div className="text-xs text-zinc-500">クイック</div>
        <div className="flex flex-wrap gap-1.5">
          {QUICKS.map((q) => (
            <button key={q.label} type="button" onClick={() => onChange(q.value())} className={CHIP_CLASS}>
              {q.label}
            </button>
          ))}
        </div>
      </div>

      {recommended.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs text-zinc-500">おすすめ時間（クリックで反映）</div>
          <div className="flex flex-wrap gap-1.5">
            {recommended.map((s) => (
              <button
                key={s.hour}
                type="button"
                onClick={() => onChange(nextJstHm(s.hour))}
                className={CHIP_CLASS}
              >
                {s.hour}時{s.tier === "best" ? "（最有力）" : ""}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-1">
        <div className="text-xs text-zinc-500">細かく指定（JST）</div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-sm text-zinc-400">
            日付
            <select value={cur.date || today} onChange={(e) => setDate(e.target.value)} className={SELECT_CLASS}>
              {dates.map((d) => (
                <option key={d} value={d}>
                  {dateLabel(d)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5 text-sm text-zinc-400">
            時刻
            <select value={cur.time || "21:00"} onChange={(e) => setTime(e.target.value)} className={SELECT_CLASS}>
              {times.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {cur.date && cur.time && (
        <div className="rounded-md border border-sky-900/50 bg-sky-950/30 px-3 py-2 text-sm text-sky-200">
          → {dateLabel(cur.date)} {cur.time} に予約します
        </div>
      )}
    </div>
  );
}
