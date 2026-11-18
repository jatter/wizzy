<script setup lang="ts">
import { computed, ref } from 'vue';
import { Check, Minus } from 'lucide-vue-next';
import { permissionBrief, permissionRows, unknownPermissions, type ScopeAction } from './permissions';

const props = defineProps<{ permissions: string[]; variant: 'granted' | 'matrix' }>();
const showCodes = ref(false);
const brief = computed(() => permissionBrief(props.permissions));
const unknown = computed(() => unknownPermissions(props.permissions));
const rows = computed(() => {
  const all = permissionRows(props.permissions);
  return props.variant === 'matrix' ? all : all.filter(row => row.granted > 0);
});
function visibleActions(actions: ScopeAction[]) {
  return props.variant === 'matrix' ? actions : actions.filter(action => action.granted);
}
</script>

<template>
  <div class="perm-scope">
    <p class="perm-brief">{{ brief }}</p>
    <div v-if="rows.length || unknown.length" class="perm-tools">
      <p v-if="variant === 'matrix'" class="perm-legend">
        <span class="perm-chip is-on"><Check :size="12" aria-hidden="true" />已授予</span>
        <span class="perm-chip is-off"><Minus :size="12" aria-hidden="true" />未授予</span>
      </p>
      <button type="button" class="perm-toggle" :aria-pressed="showCodes" @click="showCodes = !showCodes">{{ showCodes ? '隐藏权限标识' : '显示权限标识' }}</button>
    </div>
    <table v-if="rows.length || unknown.length" class="perm-matrix">
      <caption class="sr-only">按功能列出的权限</caption>
      <colgroup><col class="perm-col-name" /><col /><col class="perm-col-count" /></colgroup>
      <thead><tr><th scope="col">功能</th><th scope="col">可以做的事</th><th scope="col">范围</th></tr></thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id">
          <th scope="row">{{ row.label }}</th>
          <td>
            <ul class="perm-actions">
              <li v-for="action in visibleActions(row.actions)" :key="action.code">
                <span class="perm-chip" :class="action.granted ? 'is-on' : 'is-off'" :data-granted="action.granted ? 'true' : 'false'" :title="action.detail">
                  <Check v-if="action.granted" :size="12" aria-hidden="true" />
                  <Minus v-else :size="12" aria-hidden="true" />
                  <span>{{ action.short }}</span>
                  <code v-if="showCodes">{{ action.code }}</code>
                  <span class="sr-only">{{ action.detail }}。{{ action.granted ? '已授予' : '未授予' }}。</span>
                </span>
              </li>
            </ul>
            <p v-if="variant === 'granted' && row.granted < row.total" class="perm-missing">未包含：{{ row.actions.filter(action => !action.granted).map(action => action.short).join('、') }}</p>
          </td>
          <td>{{ row.granted }}/{{ row.total }}</td>
        </tr>
        <tr v-if="unknown.length">
          <th scope="row">未收录</th>
          <td><code v-for="code in unknown" :key="code" class="perm-unknown">{{ code }}</code></td>
          <td>{{ unknown.length }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
