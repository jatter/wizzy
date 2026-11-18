<script setup>
import { computed } from 'vue';
import { LayoutDashboard, Camera, BookOpen, Sparkles, CircleCheck, ChevronRight, ChevronLeft, ChevronDown, ArrowUpRight, ArrowRight, Bell, Search, Settings2, ShieldCheck, Users, GraduationCap, School, LibraryBig, Cpu, ListChecks, ClipboardList, ChartNoAxesCombined, CalendarDays, Heart, LockKeyhole, Plus, Upload, Download, FileText, X, MoreHorizontal, RotateCw, Trash2, Check, Clock3, CircleHelp, CircleAlert, LogOut, Menu, Image, SlidersHorizontal, Layers3, Activity, Target, RefreshCw, Flag, Pencil, Link, Zap } from 'lucide-vue-next';
const props=defineProps({name:String,size:{default:20}});
const icons={dashboard:LayoutDashboard,camera:Camera,book:BookOpen,sparkles:Sparkles,check:CircleCheck,right:ChevronRight,left:ChevronLeft,down:ChevronDown,up:ArrowUpRight,arrow:ArrowRight,bell:Bell,search:Search,settings:Settings2,shield:ShieldCheck,users:Users,student:GraduationCap,school:School,library:LibraryBig,cpu:Cpu,tasks:ListChecks,clipboard:ClipboardList,chart:ChartNoAxesCombined,calendar:CalendarDays,heart:Heart,lock:LockKeyhole,plus:Plus,upload:Upload,download:Download,file:FileText,x:X,more:MoreHorizontal,rotate:RotateCw,trash:Trash2,tick:Check,clock:Clock3,help:CircleHelp,alert:CircleAlert,logout:LogOut,menu:Menu,image:Image,filters:SlidersHorizontal,layers:Layers3,activity:Activity,target:Target,refresh:RefreshCw,flag:Flag,pencil:Pencil,link:Link,zap:Zap};
const icon=computed(()=>icons[props.name]||CircleHelp);
</script>
<template><component :is="icon" :size="size" :stroke-width="1.8" aria-hidden="true" /></template>
