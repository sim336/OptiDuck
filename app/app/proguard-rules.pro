# 本项目仅在 release 开启 R8/ProGuard。
# org.json 由 android jar 提供，无需额外规则。
# Compose / Activity 组件规则由 AGP 与 Compose 库自带的 consumer 规则覆盖。

# 保留 Application 类名（若清单引用）
-keep class com.optiduck.board.** { *; }