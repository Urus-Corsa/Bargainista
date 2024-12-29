let player = {
  move: undefined,
  score: parseInt(localStorage.getItem('playerScore'), 10) || 0
};
let computer = {
  move: undefined,
  'score': parseInt(localStorage.getItem('computerScore'), 10) || 0
};
let gameStats = {
  ['tiedRounds']: parseInt(localStorage.getItem('ties'), 10) || 0,
  show: function showStats(){
    const scoreBoard = `-------- SCORE BOARD --------\nYour Score: ${player.score}\nComputer's Score: ${computer.score}\nTied Rounds: ${gameStats.tiedRounds}`;
    return scoreBoard;
  },
  reset: function resetGameStats(){ // functions stored inside of an object == methods
    localStorage.clear()
    localStorage.setItem('playerScore', '0');
    localStorage.setItem('computerScore', '0');
    localStorage.setItem('ties', '0');
    // Reset in-memory objects
    player.score = 0;
    computer.score = 0;
    this.tiedRounds = 0;
    return 'All the game statistics and scores have been reset!';
  }
};

function setComputersMove(){
  const randomNumber = Math.random();
  // Ternary Operator
  let computersMove = 
  randomNumber < 1/3 ? 'rock' : 
  randomNumber < 2/3 ? 'paper' : 
  'scissors';
  computer.move = computersMove;
}

function determineWinner(){
  setComputersMove();
  let result = `Computer picked ${computer.move}.\n`;
  if(player.move === computer.move){
    gameStats.tiedRounds++;
    localStorage.setItem('ties', (gameStats.tiedRounds).toString());
    result += 'It\'s a tie!';
  } else if (player.move === 'paper' && computer.move === 'rock' || player.move === 'rock' && computer.move === 'paper'){
    if(computer.move === 'paper'){
      computer.score++;
      localStorage.setItem('computerScore', (computer.score).toString());
      result += 'Computer wins!';
    }else{
      player.score++;
      localStorage.setItem('playerScore', (player.score).toString());
      result += 'You win!';
    }
  } else if (player.move === 'paper' && computer.move === 'scissors' || player.move === 'scissors' && computer.move === 'paper'){
    if(computer.move === 'scissors'){
      computer.score ++;
      localStorage.setItem('computerScore', (computer.score).toString());
      result += 'Computer wins!';
    }else{
      player.score++;
      localStorage.setItem('playerScore', (player.score).toString());
      result += 'You win!';
    }
  } else if (player.move === 'rock' && computer.move === 'scissors' || player.move === 'scissors' && computer.move === 'rock'){
    if(computer.move === 'rock'){
      computer.score++;
      localStorage.setItem('computerScore', (computer.score).toString());
      result += 'Computer wins!';
    }else{
      player.score++;
      localStorage.setItem('playerScore', (player.score).toString());
      result += 'You win!';
    }
  } else {
    result = `An error has occurred in determineWinner()\nPlayer: ${player.move}\nComputer: ${computer.move}`;
  }
  result += `\n${gameStats.show()}`
  return result;
}