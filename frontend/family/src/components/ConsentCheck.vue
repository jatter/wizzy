<script setup lang="ts">
defineOptions({ inheritAttrs: false });
defineProps<{ modelValue: boolean; disabled?: boolean; label: string }>();
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>();
const nativeLabel = 'label', nativeInput = 'input';
function change(event: Event) { emit('update:modelValue', (event.target as HTMLInputElement).checked); }
function uniChange(event: { detail: { value: string[] } }) { emit('update:modelValue', event.detail.value.includes('accepted')); }
</script>

<template>
  <!-- #ifdef H5 -->
  <component :is="nativeLabel" class="check-row" v-bind="$attrs">
    <component :is="nativeInput" class="consent-checkbox" type="checkbox" :checked="modelValue" :disabled="disabled" @change="change" />
    <text>{{ label }}</text>
  </component>
  <!-- #endif -->
  <!-- #ifndef H5 -->
  <checkbox-group :class="$attrs.class" @change="uniChange"><label class="check-row"><checkbox value="accepted" :checked="modelValue" :disabled="disabled" color="#087e80" /><text>{{ label }}</text></label></checkbox-group>
  <!-- #endif -->
</template>
