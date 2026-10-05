use super::wgc::Pixels;
use base64::Engine;

pub fn crop(pixels:Pixels,x:f64,y:f64,width:f64,height:f64)->Result<Pixels,String> {
    if ![x,y,width,height].iter().all(|v|v.is_finite()) || width<2.0 || height<2.0 {return Err("Invalid capture rectangle".into());}
    let left=x.floor().max(0.0).min(pixels.width as f64) as usize;
    let top=y.floor().max(0.0).min(pixels.height as f64) as usize;
    let right=(x+width).ceil().max(0.0).min(pixels.width as f64) as usize;
    let bottom=(y+height).ceil().max(0.0).min(pixels.height as f64) as usize;
    if right<=left+1 || bottom<=top+1 {return Err("Capture rectangle outside display".into());}
    let mut data=Vec::with_capacity((right-left)*(bottom-top)*4);
    for row in top..bottom {data.extend_from_slice(&pixels.bgra[(row*pixels.width as usize+left)*4..(row*pixels.width as usize+right)*4]);}
    Ok(Pixels{width:(right-left) as u32,height:(bottom-top) as u32,bgra:data})
}
pub fn data_url(mut pixels:Pixels)->Result<String,String> {
    for pixel in pixels.bgra.chunks_exact_mut(4){pixel.swap(0,2);pixel[3]=255;}
    let mut bytes=Vec::new();
    {
        let mut encoder=png::Encoder::new(&mut bytes,pixels.width,pixels.height);
        encoder.set_color(png::ColorType::Rgba);encoder.set_depth(png::BitDepth::Eight);
        encoder.write_header().map_err(|e|e.to_string())?.write_image_data(&pixels.bgra).map_err(|e|e.to_string())?;
    }
    Ok(format!("data:image/png;base64,{}",base64::engine::general_purpose::STANDARD.encode(bytes)))
}
#[cfg(test)]mod tests {
    use super::*;
    #[test]fn clips_negative_origin_and_rounds_outward(){
        let p=Pixels{width:10,height:8,bgra:vec![0;10*8*4]};let c=crop(p,-0.2,1.2,5.0,3.1).unwrap();assert_eq!((c.width,c.height),(5,4));
    }
    #[test]fn rejects_nonfinite(){let p=Pixels{width:10,height:8,bgra:vec![0;320]};assert!(crop(p,f64::NAN,0.0,3.0,3.0).is_err());}
}
