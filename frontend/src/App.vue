<script setup lang="ts">
</script>

<template>
  <div class="app-container">
    <nav class="nav-bar">
      <span class="logo">LayerKG</span>
      <div class="nav-links">
        <router-link to="/">对话</router-link>
        <router-link to="/graph">图谱</router-link>
        <router-link to="/traces">Traces</router-link>
      </div>
    </nav>
    <router-view />
  </div>
</template>

<style>
.app-container {
  min-height: 100vh;
}

.nav-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 56px;
  background: rgba(15, 23, 42, 0.85);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid transparent;
  border-image: linear-gradient(90deg, transparent, rgba(139,92,246,0.3), rgba(59,130,246,0.3), transparent) 1;
  position: sticky;
  top: 0;
  z-index: 100;
}

.nav-bar::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(139,92,246,0.6), rgba(59,130,246,0.6), rgba(52,211,153,0.3), transparent);
  background-size: 200% 100%;
  animation: shimmer 3s ease-in-out infinite;
}

.logo {
  font-size: 20px;
  font-weight: 700;
  background: linear-gradient(135deg, #a78bfa, #60a5fa, #a78bfa);
  background-size: 200% auto;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  animation: shimmer 4s linear infinite;
}

.nav-links {
  display: flex;
  gap: 4px;
}

.nav-links a {
  color: var(--text-secondary);
  text-decoration: none;
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  position: relative;
  transition: color var(--transition-fast);
}

.nav-links a::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 50%;
  width: 0;
  height: 2px;
  background: linear-gradient(90deg, var(--primary), var(--primary-blue));
  border-radius: 1px;
  transform: translateX(-50%);
  transition: width var(--transition-normal) var(--ease-smooth), box-shadow var(--transition-normal);
}

.nav-links a:hover {
  color: var(--text-primary);
}

.nav-links a:hover::after {
  width: 60%;
  box-shadow: 0 0 8px rgba(139,92,246,0.3);
}

.nav-links a.router-link-active {
  color: #fff;
}

.nav-links a.router-link-active::after {
  width: 80%;
  box-shadow: 0 0 12px rgba(139,92,246,0.4);
}
</style>
