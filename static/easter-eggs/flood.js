(function registerFloodEffect(global) {
    const registry = global.EasterEggRegistry;
    if (!registry) {
        console.error('[easter-eggs:flood] 未找到 EasterEggRegistry，无法注册特效');
        return;
    }

    registry.register('flood', function createFloodEffect(context = {}) {
        const root = context.root;
        if (!root) {
            throw new Error('缺少彩蛋根节点');
        }

        const payload = context.payload || {};
        const wrapper = document.createElement('div');
        wrapper.className = 'easter-egg-water';

        const container = document.createElement('div');
        container.className = 'easter-egg-water-container';

        wrapper.appendChild(container);
        root.appendChild(wrapper);

        const waves = createWaves(container);

        const state = {
            payload,
            root,
            wrapper,
            container,
            waves,
            styleNode: null,
            retreatPromise: null,
            retreatTimeout: null
        };

        runFloodAnimation(state);

        return {
            /**
             * 销毁特效。
             * @param {{immediate?: boolean}} options
             */
            destroy(options = {}) {
                const immediate = Boolean(options.immediate);
                if (immediate) {
                    cleanupFloodState(state);
                    return null;
                }
                if (state.retreatPromise) {
                    return state.retreatPromise;
                }
                return startFloodRetreat(state);
            }
        };
    });

    function createWaves(container) {
        const waves = [];
        for (let i = 1; i <= 3; i++) {
            const wave = document.createElement('div');
            wave.className = `wave wave${i}`;
            container.appendChild(wave);
            waves.push(wave);
        }
        return waves;
    }

    function runFloodAnimation(state) {
        const { container, waves, payload } = state;
        if (!container || !waves.length) {
            return;
        }

        const styleEl = document.createElement('style');
        styleEl.setAttribute('data-easter-egg', 'flood');
        document.head.appendChild(styleEl);
        state.styleNode = styleEl;
        const sheet = styleEl.sheet;
        if (!sheet) {
            return;
        }

        container.style.animation = 'none';
        container.style.height = '0%';
        void container.offsetHeight;

        const range = Array.isArray(payload.intensity_range) && payload.intensity_range.length === 2
            ? payload.intensity_range
            : [0.85, 0.92];
        const minHeight = Math.min(range[0], range[1]);
        const maxHeight = Math.max(range[0], range[1]);
        const targetHeight = randRange(minHeight * 100, maxHeight * 100);
        const riseDuration = randRange(30, 40);
        const easing = pickOne([
            'ease-in-out',
            'cubic-bezier(0.25, 0.46, 0.45, 0.94)',
            'cubic-bezier(0.42, 0, 0.58, 1)'
        ]) || 'ease-in-out';
        const riseName = uniqueKey('flood_rise');
        sheet.insertRule(
            `@keyframes ${riseName} { 0% { height: 0%; } 100% { height: ${targetHeight}%; } }`,
            sheet.cssRules.length
        );
        container.style.animation = `${riseName} ${riseDuration}s ${easing} forwards`;

        const directionSets = [
            [1, 1, -1],
            [1, -1, -1],
            [-1, 1, 1],
            [-1, -1, 1]
        ];
        const directions = pickOne(directionSets) || [1, -1, 1];
        const colors = [
            'rgba(135, 206, 250, 0.35)',
            'rgba(100, 181, 246, 0.45)',
            'rgba(33, 150, 243, 0.4)'
        ];

        waves.forEach((wave, index) => {
            const svgData = buildFloodWaveShape(index);
            const color = colors[index % colors.length];
            const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${svgData.width} 1000" preserveAspectRatio="none"><path d="${svgData.path}" fill="${color}"/></svg>`;
            const encoded = encodeURIComponent(svg);
            wave.style.backgroundImage = `url("data:image/svg+xml,${encoded}")`;
            wave.style.backgroundSize = `${svgData.waveWidth}px 100%`;

            const animationName = uniqueKey(`flood_wave_${index}`);
            const startPosition = randInt(-200, 200);
            const distance = svgData.waveWidth * (directions[index] || 1);
            const duration = index === 0 ? randRange(16, 22) : index === 1 ? randRange(11, 16) : randRange(7, 12);
            const delay = randRange(0, 1.5);
            sheet.insertRule(
                `@keyframes ${animationName} { 0% { background-position-x: ${startPosition}px; } 100% { background-position-x: ${startPosition + distance}px; } }`,
                sheet.cssRules.length
            );
            wave.style.animation = `${animationName} ${duration}s linear infinite`;
            wave.style.animationDelay = `${delay}s`;
            wave.style.backgroundPositionX = `${startPosition}px`;
        });
    }

    function startFloodRetreat(state) {
        const { container } = state;
        if (!container) {
            cleanupFloodState(state);
            return Promise.resolve();
        }

        state.retreatPromise = new Promise((resolve) => {
            const measuredHeight = container.offsetHeight || container.clientHeight;
            const computedHeight = window.getComputedStyle(container).height;
            const currentHeight = measuredHeight
                ? `${measuredHeight}px`
                : (computedHeight && computedHeight !== 'auto' ? computedHeight : '0px');
            container.style.animation = 'none';
            container.style.transition = 'none';
            container.style.height = currentHeight;
            void container.offsetHeight;

            const retreatDuration = 8;
            container.style.transition = `height ${retreatDuration}s ease-in-out`;
            requestAnimationFrame(() => {
                container.style.height = '0px';
            });

            state.retreatTimeout = window.setTimeout(() => {
                container.style.transition = 'none';
                cleanupFloodState(state);
                resolve();
            }, retreatDuration * 1000);
        });

        return state.retreatPromise;
    }

    function cleanupFloodState(state) {
        if (state.retreatTimeout) {
            clearTimeout(state.retreatTimeout);
            state.retreatTimeout = null;
        }
        clearFloodAnimations(state);
        teardownFloodStyle(state);
        if (state.wrapper && state.wrapper.parentNode) {
            state.wrapper.parentNode.removeChild(state.wrapper);
        }
        state.wrapper = null;
        state.container = null;
        state.waves = [];
        state.retreatPromise = null;
    }

    function clearFloodAnimations(state) {
        const { container, waves } = state;
        if (!container) {
            return;
        }
        container.style.animation = 'none';
        container.style.transition = 'none';
        container.style.height = '0%';
        waves.forEach((wave) => {
            wave.style.animation = 'none';
            wave.style.backgroundImage = '';
            wave.style.backgroundSize = '';
            wave.style.backgroundPositionX = '0px';
        });
    }

    function teardownFloodStyle(state) {
        if (state.styleNode && state.styleNode.parentNode) {
            state.styleNode.parentNode.removeChild(state.styleNode);
        }
        state.styleNode = null;
    }

    function buildFloodWaveShape(layerIndex) {
        const baseHeight = 180 + layerIndex * 12;
        const cycles = 4;
        let path = `M0,${baseHeight}`;
        let currentX = 0;
        let previousAmplitude = randRange(40, 80);

        for (let i = 0; i < cycles; i++) {
            const waveLength = randRange(700, 900);
            const minAmp = Math.max(20, previousAmplitude - 20);
            const maxAmp = Math.min(90, previousAmplitude + 20);
            const amplitude = randRange(minAmp, maxAmp);
            previousAmplitude = amplitude;
            const halfWave = waveLength / 2;

            const peakX = currentX + halfWave / 2;
            path += ` Q${peakX},${baseHeight - amplitude} ${currentX + halfWave},${baseHeight}`;

            const troughX = currentX + halfWave + halfWave / 2;
            path += ` Q${troughX},${baseHeight + amplitude} ${currentX + waveLength},${baseHeight}`;

            currentX += waveLength;
        }

        path += ` L${currentX},1000 L0,1000 Z`;
        return {
            path,
            width: currentX,
            waveWidth: currentX / cycles
        };
    }

    function randRange(min, max) {
        return Math.random() * (max - min) + min;
    }

    function randInt(min, max) {
        return Math.floor(randRange(min, max + 1));
    }

    function pickOne(arr) {
        if (!Array.isArray(arr) || arr.length === 0) {
            return null;
        }
        return arr[Math.floor(Math.random() * arr.length)];
    }

    function uniqueKey(prefix) {
        const random = Math.random().toString(36).slice(2, 7);
        return `${prefix}_${Date.now()}_${random}`;
    }
})(window);
