/* v6 icons — thick cartoon stroke */
const Icon = ({ children, size = 20, color = "currentColor", strokeWidth = 2.5, ...rest }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" {...rest}>{children}</svg>
);
const IconArrow = (p) => <Icon {...p}><path d="M5 12h14M13 6l6 6-6 6" /></Icon>;
const IconCheck = (p) => <Icon {...p}><path d="M5 12.5l4.5 4.5L19 7" /></Icon>;
const IconClose = (p) => <Icon {...p}><path d="M6 6l12 12M18 6L6 18" /></Icon>;
const IconBolt = (p) => <Icon {...p}><path d="M13 3L4 14h7l-1 7 9-11h-7l1-7z" /></Icon>;
const IconFire = (p) => <Icon {...p}><path d="M12 3c1 4 5 5 5 10a5 5 0 11-10 0c0-2 1-3 2-4 0 2 1 3 3 3 0-3-2-5 0-9z" /></Icon>;
const IconTimer = (p) => <Icon {...p}><circle cx="12" cy="13" r="8" /><path d="M12 9v4l3 2M9 3h6" /></Icon>;
const IconChart = (p) => <Icon {...p}><path d="M4 20h16M7 17V11M12 17V6M17 17v-9" /></Icon>;
const IconBrain = (p) => <Icon {...p}><path d="M9 5a3 3 0 00-3 3v0a3 3 0 00-2 5 3 3 0 002 4 3 3 0 003 3 3 3 0 003-1V8a3 3 0 00-3-3zM15 5a3 3 0 013 3v0a3 3 0 012 5 3 3 0 01-2 4 3 3 0 01-3 3 3 3 0 01-3-1" /></Icon>;
const IconTrophy = (p) => <Icon {...p}><path d="M7 4h10v4a5 5 0 01-10 0V4zM4 5h3M17 5h3M9 14h6M10 18h4M12 14v4" /></Icon>;
const IconStar = (p) => <Icon {...p}><path d="M12 4l2.4 5 5.6.8-4 4 1 5.6L12 16.8 6.9 19.4l1-5.6-4-4 5.6-.8L12 4z" /></Icon>;
const IconCoin = (p) => <Icon {...p}><circle cx="12" cy="12" r="8" /><path d="M9.5 9.5h4a1.5 1.5 0 010 3h-3a1.5 1.5 0 000 3h4" /></Icon>;
const IconBook = (p) => <Icon {...p}><path d="M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3V4zM5 17a3 3 0 013-3h11" /></Icon>;
const IconCanvas = (p) => <Icon {...p}><path d="M4 6a2 2 0 012-2h12a2 2 0 012 2v9H4V6zM2 18h20M9 21h6" /></Icon>;
const IconPeople = (p) => <Icon {...p}><circle cx="9" cy="9" r="3" /><circle cx="17" cy="11" r="2.2" /><path d="M3 19c0-3 3-5 6-5s6 2 6 5M14 19c0-2 2-3.5 4-3.5" /></Icon>;
const IconMedal = (p) => <Icon {...p}><circle cx="12" cy="15" r="5" /><path d="M8 4l4 6 4-6M9 14l3 2 3-2" /></Icon>;
const IconLock = (p) => <Icon {...p}><rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8 10V7a4 4 0 018 0v3" /></Icon>;
const IconShield = (p) => <Icon {...p}><path d="M12 3l8 3v5c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-3z" /></Icon>;
const IconChevron = (p) => <Icon {...p}><path d="M6 9l6 6 6-6" /></Icon>;
const IconSparkle = (p) => <Icon {...p}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z" /></Icon>;
const IconSun = (p) => <Icon {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5" /></Icon>;
const IconMoon = (p) => <Icon {...p}><path d="M20 14a8 8 0 11-10-10 7 7 0 0010 10z" /></Icon>;
Object.assign(window, { Icon, IconArrow, IconCheck, IconClose, IconBolt, IconFire, IconTimer, IconChart, IconBrain, IconTrophy, IconStar, IconCoin, IconBook, IconCanvas, IconPeople, IconMedal, IconLock, IconShield, IconChevron, IconSparkle, IconSun, IconMoon });
