---
name: vue3
description: Vue 3 + TypeScript + Vite development best practices
---

## Vue 3 Development Standards

### Composition API
- Always use `<script setup lang="ts">` syntax
- Use `defineProps` with TypeScript generics: `defineProps<{title: string}>()`
- Use `defineEmits` with TypeScript: `defineEmits<{(e: 'update', val: string): void}>()`
- Prefer `ref()` for primitives, `reactive()` for objects
- Use `shallowRef()` for large objects that replace entirely (not mutate)

### File Structure
```
src/
  components/     # Reusable UI components
  composables/    # use* hooks (useAuth, useTable, etc.)
  views/          # Page-level components
  stores/         # Pinia stores
  types/          # TypeScript type definitions
  utils/          # Pure utility functions
  api/            # API request functions
```

### Naming
- Components: PascalCase (`UserProfile.vue`)
- Composables: camelCase with `use` prefix (`useAuth.ts`)
- Stores: camelCase with `use` prefix + `Store` suffix (`useUserStore.ts`)
- Types: PascalCase with no prefix (`UserInfo`, not `IUserInfo`)

### Performance
- Use `v-once` for static content
- Use `v-memo` for expensive list renders
- Lazy load routes: `() => import('./views/About.vue')`
- Use `keep-alive` with `include` to cache specific components

### Common Patterns
- Props down, events up (no direct parent mutation)
- Use `provide/inject` for deep prop drilling
- Use Pinia for shared state, not event bus
- Always use `watchEffect` cleanup: `watchEffect((onCleanup) => { ... onCleanup(() => ...) })`
