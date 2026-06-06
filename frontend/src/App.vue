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
  background: rgba(15, 23, 42, 0.8);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid transparent;
  border-image: linear-gradient(90deg, transparent, rgba(139,92,246,0.3), rgba(59,130,246,0.3), transparent) 1;
  position: sticky;
  top: 0;
  z-index: 100;
}

.logo {
  font-size: 20px;
  font-weight: 700;
  background: linear-gradient(135deg, #a78bfa, #60a5fa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
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
  transition: color var(--transition-fast), background var(--transition-fast);
}

.nav-links a:hover {
  color: var(--text-primary);
  background: rgba(148,163,184,0.08);
}

.nav-links a.router-link-active {
  color: #fff;
  background: linear-gradient(135deg, rgba(139,92,246,0.2), rgba(59,130,246,0.2));
  box-shadow: 0 0 12px rgba(139,92,246,0.15);
}
</style>
