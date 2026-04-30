/* Geometric icon set — original. Stroke-based, 24x24 grid. */
const Icon = ({ children, size = 24, color = "currentColor", strokeWidth = 2.4, ...rest }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} fill="none"
    stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" {...rest}>
    {children}
  </svg>
);

const IconArrow = (p) => <Icon {...p}><path d="M5 12h14M13 6l6 6-6 6"/></Icon>;
const IconCheck = (p) => <Icon {...p}><path d="M5 12.5l4.5 4.5L19 7"/></Icon>;
const IconClose = (p) => <Icon {...p}><path d="M6 6l12 12M18 6L6 18"/></Icon>;
const IconBolt  = (p) => <Icon {...p}><path d="M13 3L4 14h7l-1 7 9-11h-7l1-7z"/></Icon>;
const IconFire  = (p) => <Icon {...p}><path d="M12 3c1 4 5 5 5 10a5 5 0 11-10 0c0-2 1-3 2-4 0 2 1 3 3 3 0-3-2-5 0-9z"/></Icon>;
const IconTimer = (p) => <Icon {...p}><circle cx="12" cy="13" r="8"/><path d="M12 9v4l3 2M9 3h6"/></Icon>;
const IconChart = (p) => <Icon {...p}><path d="M4 20h16M7 17V11M12 17V6M17 17v-9"/></Icon>;
const IconBrain = (p) => <Icon {...p}><path d="M9 5a3 3 0 00-3 3v0a3 3 0 00-2 5 3 3 0 002 4 3 3 0 003 3 3 3 0 003-1V8a3 3 0 00-3-3zM15 5a3 3 0 013 3v0a3 3 0 012 5 3 3 0 01-2 4 3 3 0 01-3 3 3 3 0 01-3-1"/></Icon>;
const IconTrophy = (p) => <Icon {...p}><path d="M7 4h10v4a5 5 0 01-10 0V4zM4 5h3M17 5h3M9 14h6M10 18h4M12 14v4"/></Icon>;
const IconStar = (p) => <Icon {...p}><path d="M12 4l2.4 5 5.6.8-4 4 1 5.6L12 16.8 6.9 19.4l1-5.6-4-4 5.6-.8L12 4z"/></Icon>;
const IconCoin = (p) => <Icon {...p}><circle cx="12" cy="12" r="8"/><path d="M9.5 9.5h4a1.5 1.5 0 010 3h-3a1.5 1.5 0 000 3h4"/></Icon>;
const IconBook = (p) => <Icon {...p}><path d="M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3V4zM5 17a3 3 0 013-3h11"/></Icon>;
const IconCalendar = (p) => <Icon {...p}><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></Icon>;
const IconSparkle = (p) => <Icon {...p}><path d="M12 4v6M12 14v6M4 12h6M14 12h6M7 7l3 3M14 14l3 3M17 7l-3 3M10 14l-3 3"/></Icon>;
const IconCanvas = (p) => <Icon {...p}><path d="M4 6a2 2 0 012-2h12a2 2 0 012 2v9H4V6zM2 18h20M9 21h6"/></Icon>;
const IconPeople = (p) => <Icon {...p}><circle cx="9" cy="9" r="3"/><circle cx="17" cy="11" r="2.2"/><path d="M3 19c0-3 3-5 6-5s6 2 6 5M14 19c0-2 2-3.5 4-3.5"/></Icon>;
const IconMedal = (p) => <Icon {...p}><circle cx="12" cy="15" r="5"/><path d="M8 4l4 6 4-6M9 14l3 2 3-2"/></Icon>;
const IconShield = (p) => <Icon {...p}><path d="M12 3l8 3v5c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-3z"/></Icon>;
const IconChevron = (p) => <Icon {...p}><path d="M6 9l6 6 6-6"/></Icon>;
const IconLogo = ({ size = 24, color = "currentColor" }) => (
  <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
    <path d="M4 18V6l5 7 5-7v12" stroke={color} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
    <path d="M16 11l4-4M20 7h-3M20 7v3" stroke={color} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

Object.assign(window, {
  Icon, IconArrow, IconCheck, IconClose, IconBolt, IconFire, IconTimer, IconChart,
  IconBrain, IconTrophy, IconStar, IconCoin, IconBook, IconCalendar, IconSparkle,
  IconCanvas, IconPeople, IconMedal, IconShield, IconChevron, IconLogo,
});
