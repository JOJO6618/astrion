use std::{fmt, str::FromStr};

/// Unsigned decimal arithmetic for rates and costs. The coefficient is stored
/// in base 10, lowest digit first, and scale is the fractional digit count.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(super) struct Usd {
    digits: Vec<u8>,
    scale: usize,
}

impl Usd {
    fn normalize(&mut self) {
        while self.digits.last() == Some(&0) {
            self.digits.pop();
        }
        let zeros = self
            .digits
            .iter()
            .take(self.scale)
            .take_while(|&&n| n == 0)
            .count();
        self.digits.drain(..zeros);
        self.scale -= zeros;
        if self.digits.is_empty() {
            self.scale = 0;
        }
    }

    fn align(&mut self, scale: usize) {
        if scale > self.scale {
            self.digits
                .splice(..0, std::iter::repeat_n(0, scale - self.scale));
            self.scale = scale;
        }
    }

    pub(super) fn add(&mut self, other: &Self) {
        self.align(other.scale);
        let offset = self.scale - other.scale;
        self.digits
            .resize(self.digits.len().max(other.digits.len() + offset), 0);
        let mut carry = 0;
        for (index, digit) in self.digits.iter_mut().enumerate().skip(offset) {
            let sum = *digit + other.digits.get(index - offset).copied().unwrap_or(0) + carry;
            *digit = sum % 10;
            carry = sum / 10;
        }
        if carry != 0 {
            self.digits.push(carry);
        }
        self.normalize();
    }

    pub(super) fn charge(mut self, tokens: u64) -> Self {
        self.scale += 6;
        let mut carry = 0u128;
        for digit in &mut self.digits {
            let product = u128::from(*digit) * u128::from(tokens) + carry;
            *digit = (product % 10) as u8;
            carry = product / 10;
        }
        while carry > 0 {
            self.digits.push((carry % 10) as u8);
            carry /= 10;
        }
        self.normalize();
        self
    }

    /// Round to six USD places after optional scaling by 100 / percent.
    /// Retain the ledger's decimal precision until rounding the final digit.
    pub(super) fn weekly_value(&self, percent: Option<f64>) -> Option<String> {
        let (divisor, scale) = match percent {
            Some(percent) if percent > 0.0 => {
                let percent: Self = percent.to_string().parse().ok()?;
                let divisor = percent
                    .digits
                    .iter()
                    .rev()
                    .fold(0u128, |coefficient, digit| {
                        coefficient * 10 + u128::from(*digit)
                    });
                (divisor, 8 + percent.scale as isize)
            }
            Some(_) => return None,
            None => (1, 6),
        };
        let shift = scale - self.scale as isize;
        let mut remainder = 0u128;
        let mut digits: Vec<u8> = self
            .digits
            .iter()
            .rev()
            .copied()
            .chain(std::iter::repeat_n(0, shift.max(0) as usize))
            .map(|digit| {
                let dividend = remainder * 10 + u128::from(digit);
                remainder = dividend % divisor;
                (dividend / divisor) as u8
            })
            .collect();
        let discarded = shift.saturating_neg().max(0) as usize;
        let (above_half, exactly_half) = if discarded == 0 {
            (remainder * 2 > divisor, remainder * 2 == divisor)
        } else if discarded > digits.len() {
            digits.clear();
            (false, false)
        } else {
            let split = digits.len() - discarded;
            let first = digits[split];
            let tail = remainder != 0 || digits[split + 1..].iter().any(|&digit| digit != 0);
            digits.truncate(split);
            (first > 5 || (first == 5 && tail), first == 5 && !tail)
        };
        if above_half || (exactly_half && digits.last().is_some_and(|digit| digit % 2 != 0)) {
            let mut carry = 1;
            for digit in digits.iter_mut().rev() {
                let sum = *digit + carry;
                *digit = sum % 10;
                carry = sum / 10;
                if carry == 0 {
                    break;
                }
            }
            if carry != 0 {
                digits.insert(0, carry);
            }
        }
        let whole: String = digits
            .into_iter()
            .skip_while(|&digit| digit == 0)
            .map(|digit| char::from(b'0' + digit))
            .collect();
        let padded = format!("{whole:0>7}");
        let (whole, fraction) = padded.split_at(padded.len() - 6);
        Some(format!("{whole}.{fraction}"))
    }
}

impl FromStr for Usd {
    type Err = ();

    fn from_str(text: &str) -> Result<Self, Self::Err> {
        // Bound allocation for corrupt JSON, including adversarial exponents.
        const MAX_DIGITS: usize = 1024;
        let text = text.trim().strip_prefix('+').unwrap_or(text.trim());
        let (coefficient, exponent) = match text.split_once(['e', 'E']) {
            Some((number, exponent)) => (number, exponent.parse::<i32>().map_err(|_| ())?),
            None => (text, 0),
        };
        let (whole, fraction) = coefficient.split_once('.').unwrap_or((coefficient, ""));
        let count = whole.len() + fraction.len();
        if count == 0 || count > MAX_DIGITS || exponent.unsigned_abs() as usize > MAX_DIGITS {
            return Err(());
        }
        let digits = fraction
            .bytes()
            .rev()
            .chain(whole.bytes().rev())
            .map(|byte| byte.is_ascii_digit().then(|| byte - b'0').ok_or(()))
            .collect::<Result<Vec<_>, _>>()?;
        let scale = fraction.len() as i32 - exponent;
        let mut decimal = Self {
            digits,
            scale: scale.max(0) as usize,
        };
        if scale < 0 {
            decimal
                .digits
                .splice(..0, std::iter::repeat_n(0, (-scale) as usize));
        }
        decimal.normalize();
        Ok(decimal)
    }
}

impl fmt::Display for Usd {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.digits.is_empty() {
            return f.write_str("0");
        }
        for index in (0..self.digits.len().max(self.scale + 1)).rev() {
            if self.scale == index + 1 {
                f.write_str(".")?;
            }
            write!(f, "{}", self.digits.get(index).copied().unwrap_or(0))?;
        }
        Ok(())
    }
}

impl serde::Serialize for Usd {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_str(self)
    }
}

impl<'de> serde::Deserialize<'de> for Usd {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let text = String::deserialize(deserializer)?;
        text.parse()
            .map_err(|()| serde::de::Error::custom("invalid USD amount"))
    }
}

pub(super) fn parse_amount(value: &serde_json::Value) -> Option<Usd> {
    match value {
        serde_json::Value::String(text) => text.parse().ok(),
        serde_json::Value::Number(number) => number.to_string().parse().ok(),
        _ => None,
    }
}
