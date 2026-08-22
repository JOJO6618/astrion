(function initEasterEggRegistry(global) {
    /**
     * 一个极简的彩蛋注册表，用于在主应用与各特效实现之间解耦。
     * 每个特效需要调用 register(effectName, factory)，factory 必须返回带有 destroy 方法的实例。
     */
    const registry = {
        effects: Object.create(null),

        /**
         * 注册彩蛋。
         * @param {string} effectName
         * @param {(context: object) => { destroy: Function }} factory
         */
        register(effectName, factory) {
            const key = (effectName || '').trim().toLowerCase();
            if (!key) {
                console.warn('[EasterEggRegistry] 忽略空 effectName');
                return;
            }
            if (typeof factory !== 'function') {
                console.warn('[EasterEggRegistry] 注册失败，factory 不是函数:', effectName);
                return;
            }
            this.effects[key] = factory;
        },

        /**
         * 创建彩蛋实例。
         * @param {string} effectName
         * @param {object} context
         * @returns {object|null}
         */
        start(effectName, context = {}) {
            const key = (effectName || '').trim().toLowerCase();
            const factory = this.effects[key];
            if (!factory) {
                console.warn('[EasterEggRegistry] 未找到特效:', effectName);
                return null;
            }
            try {
                const instance = factory({
                    ...context,
                    effect: key,
                });
                if (!instance || typeof instance.destroy !== 'function') {
                    console.warn('[EasterEggRegistry] 特效未返回 destroy 方法:', effectName);
                    return null;
                }
                return instance;
            } catch (error) {
                console.error('[EasterEggRegistry] 特效初始化失败:', effectName, error);
                return null;
            }
        },

        has(effectName) {
            const key = (effectName || '').trim().toLowerCase();
            return Boolean(this.effects[key]);
        },

        list() {
            return Object.keys(this.effects);
        },
    };

    global.EasterEggRegistry = registry;
})(window);
