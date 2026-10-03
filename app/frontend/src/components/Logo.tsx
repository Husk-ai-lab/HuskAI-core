/** Husk AI brand mark (official artwork, public/brand/husk-logo-*.png). */
export function Logo({ size = 26 }: { size?: number }) {
  const base = size <= 32 ? 64 : size <= 64 ? 128 : 256;
  return (
    <img
      src={`/brand/husk-logo-${base}.png`}
      srcSet={`/brand/husk-logo-${base}.png 1x, /brand/husk-logo-${Math.min(base * 2, 512)}.png 2x`}
      width={size}
      height={size}
      alt=""
      aria-hidden
      draggable={false}
      style={{ display: "block", flexShrink: 0 }}
    />
  );
}

export function Brand() {
  return (
    <div className="brand">
      <Logo size={30} />
      <span>Husk AI</span>
    </div>
  );
}
