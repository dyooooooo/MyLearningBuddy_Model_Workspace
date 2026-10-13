/** 切书后丢弃过期的 openBook 结果。 */
export function epochAlive(current: number, started: number): boolean {
  return current === started;
}
