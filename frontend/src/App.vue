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
  padding: 0 20px;
  height: 50px;
  background: #2c3e50;
  color: white;
}

.logo {
  font-size: 18px;
  font-weight: bold;
}

.nav-links {
  display: flex;
  gap: 20px;
}

.nav-links a {
  color: #ecf0f1;
  text-decoration: none;
  padding: 8px 12px;
  border-radius: 4px;
  transition: background 0.2s;
}

.nav-links a:hover,
.nav-links a.router-link-active {
  background: #34495e;
}
</style>
