// @ts-nocheck
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import {

} from './shared';

export const entriesMethods = {
  upsertImageEntry(path, filename) {
    if (!path) return;
    const name = filename || path.split('/').pop() || path;
    const list = Array.isArray(this.imageEntries) ? this.imageEntries : [];
    if (list.some((item) => item.path === path)) {
      return;
    }
    this.imageEntries = [{ name, path }, ...list];
  },
  upsertVideoEntry(path, filename) {
    if (!path) return;
    const name = filename || path.split('/').pop() || path;
    const list = Array.isArray(this.videoEntries) ? this.videoEntries : [];
    if (list.some((item) => item.path === path)) {
      return;
    }
    this.videoEntries = [{ name, path }, ...list];
  },
  async fetchAllImageEntries(startPath = '') {
    const queue: string[] = [startPath || ''];
    const visited = new Set<string>();
    const results: Array<{ name: string; path: string }> = [];
    const exts = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.svg']);
    const maxFolders = 120;

    while (queue.length && visited.size < maxFolders) {
      const path = queue.shift() || '';
      if (visited.has(path)) {
        continue;
      }
      visited.add(path);
      try {
        const resp = await fetch(`/api/gui/files/entries?path=${encodeURIComponent(path)}`, {
          method: 'GET',
          credentials: 'include',
          headers: { Accept: 'application/json' }
        });
        const data = await resp.json().catch(() => null);
        if (!data?.success) {
          continue;
        }
        const items = Array.isArray(data?.data?.items) ? data.data.items : [];
        for (const item of items) {
          const rawPath =
            item?.path ||
            [path, item?.name]
              .filter(Boolean)
              .join('/')
              .replace(/\\/g, '/')
              .replace(/\/{2,}/g, '/');
          const type = String(item?.type || '').toLowerCase();
          if (type === 'directory' || type === 'folder') {
            queue.push(rawPath);
            continue;
          }
          const ext =
            String(item?.extension || '').toLowerCase() ||
            (rawPath.includes('.') ? `.${rawPath.split('.').pop()?.toLowerCase()}` : '');
          if (exts.has(ext)) {
            results.push({
              name: item?.name || rawPath.split('/').pop() || rawPath,
              path: rawPath
            });
            if (results.length >= 400) {
              return results;
            }
          }
        }
      } catch (error) {
        console.warn('遍历文件夹失败', path, error);
      }
    }
    return results;
  },
  async fetchAllVideoEntries(startPath = '') {
    const queue: string[] = [startPath || ''];
    const visited = new Set<string>();
    const results: Array<{ name: string; path: string }> = [];
    const exts = new Set(['.mp4', '.mov', '.mkv', '.avi', '.webm']);
    const maxFolders = 120;

    while (queue.length && visited.size < maxFolders) {
      const path = queue.shift() || '';
      if (visited.has(path)) {
        continue;
      }
      visited.add(path);
      try {
        const resp = await fetch(`/api/gui/files/entries?path=${encodeURIComponent(path)}`, {
          method: 'GET',
          credentials: 'include',
          headers: { Accept: 'application/json' }
        });
        const data = await resp.json().catch(() => null);
        if (!data?.success) {
          continue;
        }
        const items = Array.isArray(data?.data?.items) ? data.data.items : [];
        for (const item of items) {
          const rawPath =
            item?.path ||
            [path, item?.name]
              .filter(Boolean)
              .join('/')
              .replace(/\\/g, '/')
              .replace(/\/{2,}/g, '/');
          const type = String(item?.type || '').toLowerCase();
          if (type === 'directory' || type === 'folder') {
            queue.push(rawPath);
            continue;
          }
          const ext =
            String(item?.extension || '').toLowerCase() ||
            (rawPath.includes('.') ? `.${rawPath.split('.').pop()?.toLowerCase()}` : '');
          if (exts.has(ext)) {
            results.push({
              name: item?.name || rawPath.split('/').pop() || rawPath,
              path: rawPath
            });
            if (results.length >= 200) {
              return results;
            }
          }
        }
      } catch (error) {
        console.warn('遍历文件夹失败', path, error);
      }
    }
    return results;
  },
  async loadWorkspaceImages() {
    this.imageLoading = true;
    try {
      const entries = await this.fetchAllImageEntries('');
      this.imageEntries = entries;
      if (!entries.length) {
        this.uiPushToast({
          title: '未找到图片',
          message: '工作区内没有可用的图片文件',
          type: 'info'
        });
      }
    } catch (error) {
      console.error('加载图片列表失败', error);
      this.uiPushToast({
        title: '加载图片失败',
        message: error?.message || '请稍后重试',
        type: 'error'
      });
    } finally {
      this.imageLoading = false;
    }
  },
  async loadWorkspaceVideos() {
    this.videoLoading = true;
    try {
      const entries = await this.fetchAllVideoEntries('');
      this.videoEntries = entries;
      if (!entries.length) {
        this.uiPushToast({
          title: '未找到视频',
          message: '工作区内没有可用的视频文件',
          type: 'info'
        });
      }
    } catch (error) {
      console.error('加载视频列表失败', error);
      this.uiPushToast({
        title: '加载视频失败',
        message: error?.message || '请稍后重试',
        type: 'error'
      });
    } finally {
      this.videoLoading = false;
    }
  }
};
