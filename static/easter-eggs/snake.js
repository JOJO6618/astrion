(function registerSnakeEffect(global) {
    const registry = global.EasterEggRegistry;
    if (!registry) {
        console.error('[easter-eggs:snake] 未找到 EasterEggRegistry，无法注册特效');
        return;
    }

    registry.register('snake', function createSnakeEffect(context = {}) {
        const root = context.root;
        if (!root) {
            throw new Error('缺少彩蛋根节点');
        }
        const payload = context.payload || {};

        const container = document.createElement('div');
        container.className = 'snake-overlay';

        const canvas = document.createElement('canvas');
        canvas.className = 'snake-canvas';
        container.appendChild(canvas);
        root.appendChild(container);

        const effect = new SnakeEffect(container, canvas, payload, context.app);

        return {
            destroy(options = {}) {
                return effect.stop(options);
            }
        };
    });

    class SnakeEffect {
        constructor(container, canvas, payload, app) {
            this.container = container;
            this.canvas = canvas;
            this.ctx = canvas.getContext('2d');
            this.payload = payload || {};
            this.app = app || null;
            this.targetApples = Math.max(1, Math.floor(Number(this.payload.apples_target) || 10));
            this.initialApples = Math.max(1, Math.floor(Number(this.payload.initial_apples) || 3));
            this.apples = [];
            this.applesEaten = 0;
            this.appleFadeInDuration = 800;
            this.appleFadeOutDuration = 1500;
            this.finalRun = false;
            this.running = true;
            this.finished = false;
            this.cleanupRequested = false;
            this.cleanupResolve = null;
            this.cleanupPromise = new Promise((resolve) => {
                this.cleanupResolve = resolve;
            });
            this.appNotified = false;
            this.pendingResizeId = null;

            this.resize = this.resize.bind(this);
            window.addEventListener('resize', this.resize);
            this.resize();
            this.scheduleResize();

            this.snake = new Snake(this);

            const spawnTime = typeof performance !== 'undefined' ? performance.now() : Date.now();
            for (let i = 0; i < this.initialApples; i++) {
                this.apples.push(this.createApple(spawnTime));
            }

            this.animate = this.animate.bind(this);
            this.lastTime = 0;
            this.frameInterval = 1000 / 60;
            this.rafId = requestAnimationFrame(this.animate);
        }

        scheduleResize() {
            if (this.pendingResizeId) {
                cancelAnimationFrame(this.pendingResizeId);
            }
            this.pendingResizeId = requestAnimationFrame(() => {
                this.pendingResizeId = null;
                this.resize();
            });
        }

        resize() {
            const rect = this.container.getBoundingClientRect();
            let width = Math.floor(rect.width);
            let height = Math.floor(rect.height);

            if (width < 2 || height < 2) {
                width = window.innerWidth || document.documentElement.clientWidth || 1;
                height = window.innerHeight || document.documentElement.clientHeight || 1;
            }

            width = Math.max(1, width);
            height = Math.max(1, height);
            if (this.canvas.width !== width || this.canvas.height !== height) {
                this.canvas.width = width;
                this.canvas.height = height;
                if (this.snake && typeof this.snake.handleResize === 'function') {
                    this.snake.handleResize(width, height);
                }
            }
        }

        createApple(timestamp = null) {
            const margin = 60;
            const width = this.canvas.width;
            const height = this.canvas.height;
            const now = timestamp != null
                ? timestamp
                : (typeof performance !== 'undefined' ? performance.now() : Date.now());
            return {
                x: margin + Math.random() * Math.max(1, width - margin * 2),
                y: margin + Math.random() * Math.max(1, height - margin * 2),
                opacity: 0,
                fadeInStart: now,
                fadeOutStart: null
            };
        }

        handleAppleConsumed(index) {
            this.applesEaten += 1;
            const reachedGoal = this.applesEaten >= this.targetApples;
            if (reachedGoal) {
                this.apples.splice(index, 1);
                this.startFinalRun();
            } else if (this.apples[index]) {
                const now = typeof performance !== 'undefined' ? performance.now() : Date.now();
                this.apples[index] = this.createApple(now);
            }
        }

        startFinalRun(force = false) {
            if (this.finalRun) {
                return;
            }
            this.finalRun = true;
            this.appleFadeOutStart = typeof performance !== 'undefined' ? performance.now() : Date.now();
            this.apples.forEach((apple) => {
                apple.fadeOutStart = this.appleFadeOutStart;
            });
            this.snake.beginFinalRun();
            if (force) {
                this.applesEaten = Math.max(this.applesEaten, this.targetApples);
            }
        }

        drawApples(timestamp) {
            const ctx = this.ctx;
            const now = timestamp != null
                ? timestamp
                : (typeof performance !== 'undefined' ? performance.now() : Date.now());
            for (let i = this.apples.length - 1; i >= 0; i--) {
                const apple = this.apples[i];
                if (apple.fadeOutStart) {
                    const progress = Math.min(1, (now - apple.fadeOutStart) / this.appleFadeOutDuration);
                    apple.opacity = 1 - progress;
                    if (apple.opacity <= 0.02) {
                        this.apples.splice(i, 1);
                        continue;
                    }
                } else if (apple.fadeInStart) {
                    const progress = Math.min(1, (now - apple.fadeInStart) / this.appleFadeInDuration);
                    apple.opacity = progress;
                    if (apple.opacity >= 0.995) {
                        apple.fadeInStart = null;
                        apple.opacity = 1;
                    }
                } else {
                    apple.opacity = 1;
                }

                ctx.fillStyle = `hsla(${this.snake.hue}, 70%, 65%, ${apple.opacity})`;
                ctx.beginPath();
                ctx.arc(apple.x, apple.y, 14, 0, Math.PI * 2);
                ctx.fill();
            }
        }

        animate(timestamp) {
            if (!this.running) {
                return;
            }
            this.rafId = requestAnimationFrame(this.animate);
            const elapsed = timestamp - this.lastTime;
            if (elapsed < this.frameInterval) {
                return;
            }
            this.lastTime = timestamp - (elapsed % this.frameInterval);

            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

            this.snake.findNearestApple();
            this.snake.update();
            this.drawApples(timestamp);
            this.snake.draw();

            if (this.finalRun && this.apples.length === 0 && this.snake.isCompletelyOffscreen(80)) {
                this.finish(false, !this.cleanupRequested);
            }
        }

        stop(options = {}) {
            const immediate = Boolean(options && options.immediate);
            if (immediate) {
                this.cleanupRequested = true;
                this.finish(true, false);
                return null;
            }
            if (this.finished) {
                return this.cleanupPromise;
            }
            this.cleanupRequested = true;
            if (!this.finalRun) {
                this.startFinalRun(true);
            }
            return this.cleanupPromise;
        }

        finish(immediate = false, notifyCompletion = false) {
            if (this.finished) {
                return;
            }
            this.finished = true;
            this.running = false;
            if (this.rafId) {
                cancelAnimationFrame(this.rafId);
                this.rafId = null;
            }
            if (this.pendingResizeId) {
                cancelAnimationFrame(this.pendingResizeId);
                this.pendingResizeId = null;
            }
            window.removeEventListener('resize', this.resize);
            if (this.container && this.container.parentNode) {
                this.container.parentNode.removeChild(this.container);
            }
            if (this.cleanupResolve) {
                this.cleanupResolve();
                this.cleanupResolve = null;
            }
            if (immediate) {
                this.apples.length = 0;
            }
            if (notifyCompletion && !this.cleanupRequested) {
                this.notifyAppForCleanup();
            }
        }

        notifyAppForCleanup() {
            if (this.appNotified) {
                return;
            }
            this.appNotified = true;
            if (this.app && typeof this.app.destroyEasterEggEffect === 'function') {
                setTimeout(() => {
                    try {
                        this.app.destroyEasterEggEffect(true);
                    } catch (error) {
                        console.warn('自动清理贪吃蛇彩蛋失败:', error);
                    }
                }, 0);
            }
        }
    }

    class Snake {
        constructor(effect) {
            this.effect = effect;
            this.canvas = effect.canvas;
            this.ctx = effect.ctx;
            this.radius = 14;
            this.targetLength = 28;
            this.currentLength = 28;
            this.speed = 2;
            this.angle = 0;
            this.targetAngle = 0;
            this.hue = 30;
            this.currentTarget = null;
            this.targetStartTime = Date.now();
            this.targetTimeout = 10000;
            this.timedOut = false;
            this.finalRunAngle = null;
            this.initializeEntryPosition();
        }

        initializeEntryPosition() {
            const width = this.canvas.width;
            const height = this.canvas.height;
            const margin = 120;
            const side = Math.floor(Math.random() * 4);
            let startX;
            let startY;
            switch (side) {
                case 0:
                    startX = -margin;
                    startY = Math.random() * height;
                    break;
                case 1:
                    startX = width + margin;
                    startY = Math.random() * height;
                    break;
                case 2:
                    startX = Math.random() * width;
                    startY = -margin;
                    break;
                default:
                    startX = Math.random() * width;
                    startY = height + margin;
            }
            const targetX = width / 2;
            const targetY = height / 2;
            const entryAngle = Math.atan2(targetY - startY, targetX - startX);
            this.angle = entryAngle;
            this.targetAngle = entryAngle;
            const tailX = startX - Math.cos(entryAngle) * this.targetLength;
            const tailY = startY - Math.sin(entryAngle) * this.targetLength;
            this.path = [
                { x: startX, y: startY },
                { x: tailX, y: tailY }
            ];
        }

        handleResize(width, height) {
            this.path.forEach((point) => {
                point.x = Math.max(-40, Math.min(width + 40, point.x));
                point.y = Math.max(-40, Math.min(height + 40, point.y));
            });
        }

        findNearestApple() {
            if (this.effect.finalRun) {
                return;
            }
            const apples = this.effect.apples;
            if (!apples.length) {
                this.currentTarget = null;
                return;
            }

            const now = Date.now();
            if (this.currentTarget && (now - this.targetStartTime) < this.targetTimeout) {
                const targetStillExists = apples.includes(this.currentTarget);
                if (targetStillExists) {
                    const dx = this.currentTarget.x - this.path[0].x;
                    const dy = this.currentTarget.y - this.path[0].y;
                    this.targetAngle = Math.atan2(dy, dx);
                    this.timedOut = false;
                    return;
                }
            }

            let targetApple = null;
            let targetDistance = this.timedOut ? -Infinity : Infinity;

            apples.forEach((apple) => {
                const dx = apple.x - this.path[0].x;
                const dy = apple.y - this.path[0].y;
                const distance = Math.sqrt(dx * dx + dy * dy);

                if (this.timedOut) {
                    if (distance > targetDistance) {
                        targetDistance = distance;
                        targetApple = apple;
                    }
                } else if (distance < targetDistance) {
                    targetDistance = distance;
                    targetApple = apple;
                }
            });

            if (targetApple) {
                if (this.currentTarget !== targetApple) {
                    this.currentTarget = targetApple;
                    this.targetStartTime = now;
                    this.timedOut = false;
                } else if ((now - this.targetStartTime) >= this.targetTimeout) {
                    this.timedOut = true;
                }

                const dx = targetApple.x - this.path[0].x;
                const dy = targetApple.y - this.path[0].y;
                this.targetAngle = Math.atan2(dy, dx);
            }
        }

        beginFinalRun() {
            if (this.finalRunAngle == null) {
                this.finalRunAngle = this.angle;
            }
            this.targetAngle = this.finalRunAngle;
            this.currentTarget = null;
        }

        update() {
            if (!this.effect.finalRun) {
                let angleDiff = this.targetAngle - this.angle;
                while (angleDiff > Math.PI) angleDiff -= 2 * Math.PI;
                while (angleDiff < -Math.PI) angleDiff += 2 * Math.PI;
                const maxTurnRate = 0.03;
                this.angle += angleDiff * maxTurnRate;
            } else if (this.finalRunAngle != null) {
                this.angle = this.finalRunAngle;
            }

            const head = { ...this.path[0] };
            head.x += Math.cos(this.angle) * this.speed;
            head.y += Math.sin(this.angle) * this.speed;
            if (!this.effect.finalRun) {
                const margin = 0;
                if (head.x < -margin) head.x = -margin;
                if (head.x > this.canvas.width + margin) head.x = this.canvas.width + margin;
                if (head.y < -margin) head.y = -margin;
                if (head.y > this.canvas.height + margin) head.y = this.canvas.height + margin;
            }

            this.path.unshift(head);

            if (!this.effect.finalRun) {
                this.checkApples(head);
            }

            this.trimPath();
        }

        checkApples(head) {
            const apples = this.effect.apples;
            for (let i = 0; i < apples.length; i++) {
                const apple = apples[i];
                const dx = head.x - apple.x;
                const dy = head.y - apple.y;
                const distance = Math.sqrt(dx * dx + dy * dy);

                if (distance < this.radius + 14) {
                    this.targetLength += 28 * 3;
                    this.currentTarget = null;
                    this.effect.handleAppleConsumed(i);
                    break;
                }
            }
        }

        trimPath() {
            let pathLength = 0;
            for (let i = 0; i < this.path.length - 1; i++) {
                const dx = this.path[i].x - this.path[i + 1].x;
                const dy = this.path[i].y - this.path[i + 1].y;
                pathLength += Math.sqrt(dx * dx + dy * dy);
            }

            while (this.path.length > 2 && pathLength > this.targetLength) {
                const last = this.path[this.path.length - 1];
                const secondLast = this.path[this.path.length - 2];
                const dx = secondLast.x - last.x;
                const dy = secondLast.y - last.y;
                const segmentLength = Math.sqrt(dx * dx + dy * dy);

                if (pathLength - segmentLength >= this.targetLength) {
                    this.path.pop();
                    pathLength -= segmentLength;
                } else {
                    break;
                }
            }

            this.currentLength = pathLength;
        }

        draw() {
            if (this.path.length < 2) {
                return;
            }
            const ctx = this.ctx;
            ctx.strokeStyle = `hsl(${this.hue}, 70%, 65%)`;
            ctx.lineWidth = this.radius * 2;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.shadowBlur = 20;
            ctx.shadowColor = `hsl(${this.hue}, 80%, 55%)`;

            ctx.beginPath();
            ctx.moveTo(this.path[0].x, this.path[0].y);
            for (let i = 1; i < this.path.length; i++) {
                ctx.lineTo(this.path[i].x, this.path[i].y);
            }
            ctx.stroke();
            ctx.shadowBlur = 0;
        }

        isCompletelyOffscreen(margin = 40) {
            const width = this.canvas.width;
            const height = this.canvas.height;
            return this.path.every((point) => {
                return (
                    point.x < -margin ||
                    point.x > width + margin ||
                    point.y < -margin ||
                    point.y > height + margin
                );
            });
        }
    }
})(window);
