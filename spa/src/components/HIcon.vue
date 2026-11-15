<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none"
       :stroke="color" :stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <path v-for="(d, i) in paths" :key="i" :d="d" />
  </svg>
</template>

<script setup>
// 线性图标集 — 总方案 B0：统一 1.8 描边 / 22px 栅格
import { computed } from 'vue'
const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 22 },
  color: { type: String, default: 'currentColor' },
})

const LIB = {
  chat: ['M21 12a8 8 0 0 1-8 8H5l-2 2V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8z'],
  plus: ['M12 5v14', 'M5 12h14'],
  grid: ['M4 4h7v7H4z', 'M13 4h7v7h-7z', 'M4 13h7v7H4z', 'M13 13h7v7h-7z'],
  folder: ['M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'],
  book: ['M4 19V5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z', 'M19 17H6a2 2 0 0 0-2 2'],
  puzzle: ['M9 3h6v3a2 2 0 1 0 4 0h2v6h-3a2 2 0 1 0 0 4h3v5h-6v-3a2 2 0 1 0-4 0v3H5v-6h3a2 2 0 1 0 0-4H5V6h4z'],
  gear: ['M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z', 'M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2-1.2L14.2 3h-4L9.7 5.7a7 7 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2 1.2L9.8 21h4l.4-2.7a7 7 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z'],
  pulse: ['M3 12h4l2-7 4 14 2-7h6'],
  cube: ['M12 2 21 7v10l-9 5-9-5V7z', 'M3 7l9 5 9-5', 'M12 12v10'],
  download: ['M12 3v12', 'M7 11l5 5 5-5', 'M4 21h16'],
  dots: ['M12 5h.01', 'M12 12h.01', 'M12 19h.01'],
  clock: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 7v5l3 3'],
  palette: ['M12 21a9 9 0 1 1 9-9c0 2-1.5 3-3 3h-2a2 2 0 0 0-1.5 3.3c.3.4.5.8.5 1.2 0 .8-.7 1.5-2 1.5z', 'M7.5 11h.01', 'M10 8h.01', 'M14 8h.01'],
  shield: ['M12 22s8-3.6 8-10V5l-8-3-8 3v7c0 6.4 8 10 8 10z'],
  key: ['M14 10a4 4 0 1 0-4 4l1.5-1.5L14 15l2-2 2 2 3-3-4.5-4.5', 'M7 17h.01'],
  info: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 8h.01', 'M12 11v6'],
  send: ['M22 2 11 13', 'M22 2l-7 20-4-9-9-4z'],
  stop: ['M7 7h10v10H7z'],  // ★ 2026-10-10 #1053: 停止按钮（实心方块描边）
  search: ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M21 21l-4.35-4.35'],
  box: ['M3 8h18v12H3z', 'M3 8l2-4h14l2 4', 'M10 12h4'],
  mail: ['M3 6h18v12H3z', 'M3 7l9 6 9-6'],
  check: ['M4 12l5 5L20 7'],
  x: ['M6 6l12 12', 'M18 6L6 18'],
  mic: ['M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z', 'M5 11a7 7 0 0 0 14 0', 'M12 18v3'],
  image: ['M3 5h18v14H3z', 'M3 15l5-5 4 4 3-3 6 6', 'M9 9h.01'],
  music: ['M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z', 'M20 16a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z', 'M9 18V5l11-2v13'],
  globe: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M3 12h18', 'M12 3a13 13 0 0 1 0 18 13 13 0 0 1 0-18z'],
  users: ['M17 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2', 'M10 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M21 21v-2a4 4 0 0 0-3-3.87'],
  bulb: ['M9 18h6', 'M10 22h4', 'M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.4 1 2.3h6c0-.9.4-1.8 1-2.3A7 7 0 0 0 12 2z'],
  bot: ['M4 9h16a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1z', 'M9 14h.01', 'M15 14h.01', 'M12 9V5', 'M9.5 5h5'],
  chart: ['M4 4v16h16', 'M8 12v4', 'M13 8v8', 'M18 13v3'],
  task: ['M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z', 'M8.5 12l2.5 2.5 4.5-5'],
  heart: ['M12 20.5S4.5 15.5 3 11.2A4.4 4.4 0 0 1 12 7a4.4 4.4 0 0 1 9 4.2C19.5 15.5 12 20.5 12 20.5z'],
}

const paths = computed(() => LIB[props.name] || LIB.box)
</script>
