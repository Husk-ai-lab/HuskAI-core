import { useT } from "../i18n";

/** Line-art industrial arm inspecting documents with a lens. Original illustration. */
export function RobotArm({ working = false }: { working?: boolean }) {
  const t = useT();
  const s = { stroke: "currentColor", strokeWidth: 2, fill: "var(--surface)", strokeLinejoin: "round" as const };
  const line = { stroke: "currentColor", strokeWidth: 2, fill: "none", strokeLinecap: "round" as const };
  return (
    <svg className={`robot ${working ? "working" : ""}`} viewBox="0 0 560 520" role="img" aria-label={t("discovery.robot_alt")}>
      <ellipse cx="400" cy="440" rx="150" ry="50" fill="none" stroke="var(--border-strong)" strokeDasharray="3 5" />
      <ellipse cx="400" cy="440" rx="190" ry="68" fill="none" stroke="var(--border)" />
      {/* light cone */}
      <path d="M150 405 L96 470 L230 470 Z" fill="var(--accent)" opacity="0.14" />
      {/* documents */}
      <g>
        {[0, 1, 2].map((i) => (
          <path key={i} d={`M70 ${452 + i * 12} L200 ${430 + i * 12} L262 ${452 + i * 12} L132 ${476 + i * 12} Z`} {...s} />
        ))}
        <path d="M104 446 L196 431 M112 455 L210 438 M120 464 L212 448" stroke="var(--accent)" strokeWidth="2" />
      </g>
      {/* base */}
      <ellipse cx="400" cy="430" rx="112" ry="38" {...s} />
      <path d="M288 430 v18 a112 38 0 0 0 224 0 v-18" {...s} />
      {Array.from({ length: 10 }).map((_, i) => {
        const a = (i / 10) * Math.PI * 2;
        return <circle key={i} cx={400 + Math.cos(a) * 92} cy={430 + Math.sin(a) * 28} r="4" {...s} />;
      })}
      {/* body */}
      <path d="M345 300 v118 a55 18 0 0 0 110 0 v-118" {...s} />
      <ellipse cx="400" cy="300" rx="55" ry="18" {...s} />
      {Array.from({ length: 7 }).map((_, i) => (
        <path key={i} d={`M${358 + i * 14} ${312 + (i === 0 || i === 6 ? -4 : 0)} v108`} {...line} strokeWidth="1.5" />
      ))}
      <path d="M362 320 q8 40 -2 80" stroke="var(--accent)" strokeWidth="2" fill="none" />
      {/* arm */}
      <g className="arm">
        <circle cx="400" cy="262" r="38" {...s} />
        <circle cx="400" cy="262" r="22" {...s} />
        <circle cx="400" cy="262" r="6" fill="var(--accent)" />
        <path d="M376 236 L326 92 M398 228 L350 86" {...line} />
        <path d="M360 140 L340 110 M372 168 L352 132" {...line} strokeWidth="1.5" />
        <circle cx="338" cy="72" r="28" {...s} />
        <circle cx="338" cy="72" r="13" {...s} />
        <path d="M314 82 L192 190 M326 98 L206 206" {...line} />
        <path d="M238 156 L222 170 M262 136 L246 150" {...line} strokeWidth="1.5" />
        <rect x="170" y="186" width="34" height="26" rx="4" transform="rotate(42 187 199)" {...s} />
        <path d="M176 214 L160 300 M190 216 L172 302" {...line} />
        <g className="lens">
          <circle cx="146" cy="352" r="48" {...s} />
          <circle cx="146" cy="352" r="38" fill="none" stroke="var(--accent)" strokeWidth="2" />
          <path d="M128 372 q18 10 34 0" stroke="var(--text-2)" strokeWidth="2" fill="none" />
        </g>
      </g>
    </svg>
  );
}
