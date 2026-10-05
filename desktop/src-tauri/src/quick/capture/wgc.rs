//! One-shot Windows Graphics Capture. Called on a worker thread, never on the UI thread.
use std::time::{Duration, Instant};
use windows::core::{factory, Interface};
use windows::Graphics::Capture::{Direct3D11CaptureFramePool, GraphicsCaptureItem, GraphicsCaptureSession};
use windows::Graphics::DirectX::{Direct3D11::IDirect3DDevice, DirectXPixelFormat};
use windows::Win32::Foundation::{HMODULE, HWND};
use windows::Win32::Graphics::Direct3D::D3D_DRIVER_TYPE_HARDWARE;
use windows::Win32::Graphics::Direct3D11::*;
use windows::Win32::Graphics::Dxgi::IDXGIDevice;
use windows::Win32::Graphics::Gdi::HMONITOR;
use windows::Win32::System::WinRT::{RoInitialize, RoUninitialize, RO_INIT_MULTITHREADED};
use windows::Win32::System::WinRT::Direct3D11::{CreateDirect3D11DeviceFromDXGIDevice, IDirect3DDxgiInterfaceAccess};
use windows::Win32::System::WinRT::Graphics::Capture::IGraphicsCaptureItemInterop;

pub struct Pixels {
    pub width: u32,
    pub height: u32,
    pub bgra: Vec<u8>,
}

pub enum Target {
    Window(isize),
    Monitor(isize),
}

struct Apartment;
impl Drop for Apartment {
    fn drop(&mut self) { unsafe { RoUninitialize() }; }
}

pub fn capture(target: Target) -> Result<Pixels, String> {
    unsafe { RoInitialize(RO_INIT_MULTITHREADED) }.map_err(|e| e.to_string())?;
    let _apartment = Apartment;
    capture_inner(target).map_err(|e| format!("WGC: {e}"))
}

fn capture_inner(target: Target) -> windows::core::Result<Pixels> {
    if !GraphicsCaptureSession::IsSupported()? {
        return Err(windows::core::Error::from_hresult(windows::core::HRESULT(0x80004001u32 as i32)));
    }
    let interop: IGraphicsCaptureItemInterop = factory::<GraphicsCaptureItem, IGraphicsCaptureItemInterop>()?;
    let item: GraphicsCaptureItem = unsafe {
        match target {
            Target::Window(handle) => interop.CreateForWindow(HWND(handle as _))?,
            Target::Monitor(handle) => interop.CreateForMonitor(HMONITOR(handle as _))?,
        }
    };
    let mut device = None;
    let mut context = None;
    unsafe {
        D3D11CreateDevice(None, D3D_DRIVER_TYPE_HARDWARE, HMODULE::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, None, D3D11_SDK_VERSION,
            Some(&mut device), None, Some(&mut context))?;
    }
    let device = device.ok_or_else(windows::core::Error::from_win32)?;
    let context = context.ok_or_else(windows::core::Error::from_win32)?;
    let dxgi: IDXGIDevice = device.cast()?;
    let runtime_device: IDirect3DDevice = unsafe { CreateDirect3D11DeviceFromDXGIDevice(&dxgi)? }.cast()?;
    let pool = Direct3D11CaptureFramePool::CreateFreeThreaded(&runtime_device,
        DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, item.Size()?)?;
    let session = pool.CreateCaptureSession(&item)?;
    // Cursor is not part of the attached image. Older API versions may lack this property.
    let _ = session.SetIsCursorCaptureEnabled(false);
    session.StartCapture()?;
    let result = (|| {
        let deadline = Instant::now() + Duration::from_secs(5);
        let frame = loop {
            if let Ok(frame) = pool.TryGetNextFrame() { break frame; }
            if Instant::now() >= deadline {
                return Err(windows::core::Error::from_hresult(windows::core::HRESULT(0x800705B4u32 as i32)));
            }
            std::thread::sleep(Duration::from_millis(12));
        };
        let size = frame.ContentSize()?;
        let access: IDirect3DDxgiInterfaceAccess = frame.Surface()?.cast()?;
        let texture: ID3D11Texture2D = unsafe { access.GetInterface()? };
        let mut desc = D3D11_TEXTURE2D_DESC::default();
        unsafe { texture.GetDesc(&mut desc) };
        if size.Width <= 0 || size.Height <= 0 {
            return Err(windows::core::Error::from_hresult(windows::core::HRESULT(0x80070057u32 as i32)));
        }
        let width = (size.Width as u32).min(desc.Width);
        let height = (size.Height as u32).min(desc.Height);
        desc.Usage = D3D11_USAGE_STAGING;
        desc.BindFlags = 0;
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ.0 as u32;
        desc.MiscFlags = 0;
        let mut staging = None;
        unsafe { device.CreateTexture2D(&desc, None, Some(&mut staging))? };
        let staging = staging.ok_or_else(windows::core::Error::from_win32)?;
        unsafe { context.CopyResource(&staging, &texture) };
        let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
        unsafe { context.Map(&staging, 0, D3D11_MAP_READ, 0, Some(&mut mapped))? };
        let mut bgra = vec![0; width as usize * height as usize * 4];
        for y in 0..height as usize {
            let row = unsafe { std::slice::from_raw_parts(
                (mapped.pData as *const u8).add(y * mapped.RowPitch as usize), width as usize * 4) };
            bgra[y * width as usize * 4..(y + 1) * width as usize * 4].copy_from_slice(row);
        }
        unsafe { context.Unmap(&staging, 0) };
        frame.Close()?;
        Ok(Pixels { width, height, bgra })
    })();
    let _ = session.Close();
    let _ = pool.Close();
    result
}

impl Pixels {
    /// Debug evidence uses BMP, avoiding image encoders in the capture worker.
    pub fn save_bmp(&self, path: &std::path::Path) -> std::io::Result<()> {
        use std::io::Write;
        let size = 54 + self.bgra.len() as u32;
        let mut file = std::fs::File::create(path)?;
        file.write_all(b"BM")?;
        file.write_all(&size.to_le_bytes())?;
        file.write_all(&[0; 4])?;
        file.write_all(&54u32.to_le_bytes())?;
        file.write_all(&40u32.to_le_bytes())?;
        file.write_all(&(self.width as i32).to_le_bytes())?;
        file.write_all(&(-(self.height as i32)).to_le_bytes())?;
        file.write_all(&1u16.to_le_bytes())?;
        file.write_all(&32u16.to_le_bytes())?;
        file.write_all(&0u32.to_le_bytes())?;
        file.write_all(&(self.bgra.len() as u32).to_le_bytes())?;
        file.write_all(&[0; 16])?;
        file.write_all(&self.bgra)
    }
}
